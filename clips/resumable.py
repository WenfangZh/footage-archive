"""Resumable uploads: the server side of the tus protocol, version 1.0.0 (SPEC.md §8, M6.3).

With JavaScript, the upload page sends the file in pieces, so an upload cut off by a lost
connection or a reloaded page resumes from the last byte stored. Until its last piece arrives
it is a partial upload (clips.models.PartialUpload). The browser then posts to `finish`, which
runs the quick read and turns it into an original; the form follows as for a file sent in one
piece (clips.views.upload).

Of tus, only what the archive needs: creation, with the length declared up front and checked
against the size cap, so nothing beyond the cap is ever stored; expiration; and termination.
Upload-Metadata, where clients put the file name, is never read. CSRF checks stay on: the
page's script sends the token in a header.

A piece streams to disk with no database transaction open, since SQLite would hold up every
other write meanwhile. A lock on the file keeps two pieces of one upload from being written
at once, and the offset is read from the file under that lock.
"""

import datetime
import fcntl
import os

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import Http404, HttpResponse, UnreadablePostError
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import http_date
from django.views.decorators.http import require_http_methods, require_POST

from . import probe, views
from .forms import FileForm
from .models import Original, PartialUpload

VERSION = '1.0.0'
PIECE_TYPE = 'application/offset+octet-stream'
CHUNK_BYTES = 1024 * 1024


@require_http_methods(['OPTIONS', 'POST'])
def start(request):
    """Start a partial upload of the declared length; its address comes back in Location."""
    if request.method == 'OPTIONS':
        return tus_response(204, {
            'Tus-Version': VERSION,
            'Tus-Extension': 'creation,expiration,termination',
            'Tus-Max-Size': str(settings.UPLOAD_MAX_BYTES),
        })
    if request.headers.get('Tus-Resumable') != VERSION:
        return tus_response(412, {'Tus-Version': VERSION})
    # Counted towards the rate limit before this view (clips.rate_limits).
    if not views.uploads_open():
        return tus_response(503)
    length = whole_number(request.headers.get('Upload-Length'))
    if length is None or length == 0:
        return tus_response(400)
    if length > settings.UPLOAD_MAX_BYTES:
        return tus_response(413)

    partial = PartialUpload(length=length)
    partial.file.save('partial', ContentFile(b''))
    return tus_response(201, {
        'Location': request.build_absolute_uri(reverse('clips:resumable', args=[partial.id])),
        'Upload-Expires': expires(partial),
    })


@require_http_methods(['HEAD', 'PATCH', 'DELETE'])
def resume(request, partial_id):
    """HEAD says how much has arrived, PATCH adds a piece, DELETE abandons the upload."""
    if request.headers.get('Tus-Resumable') != VERSION:
        return tus_response(412, {'Tus-Version': VERSION})
    partial = live_or_404(partial_id)
    if request.method == 'HEAD':
        return tus_response(200, {
            'Upload-Offset': str(size_or_404(partial)),
            'Upload-Length': str(partial.length),
            'Upload-Expires': expires(partial),
        })
    if request.method == 'DELETE':
        partial.discard()
        return tus_response(204)
    return receive_piece(request, partial)


def receive_piece(request, partial):
    if request.content_type != PIECE_TYPE:
        return tus_response(415)
    offset = whole_number(request.headers.get('Upload-Offset'))
    size = whole_number(request.META.get('CONTENT_LENGTH'))
    if offset is None:
        return tus_response(400)
    if size is None:
        return tus_response(411)

    try:
        # Never created here: a partial upload deleted since it was looked up stays deleted.
        file = os.fdopen(os.open(partial.file.path, os.O_WRONLY | os.O_APPEND), 'ab')
    except FileNotFoundError:
        raise Http404
    with file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return tus_response(423)
        if offset != os.fstat(file.fileno()).st_size:
            return tus_response(409)
        if offset + size > partial.length:
            return tus_response(413)
        received = 0
        while received < size:
            try:
                chunk = request.read(min(CHUNK_BYTES, size - received))
            except (OSError, UnreadablePostError):
                break  # The connection dropped: what arrived is kept, and the browser resumes from it.
            if not chunk:
                break
            file.write(chunk)
            received += len(chunk)
    return tus_response(204, {'Upload-Offset': str(offset + received), 'Upload-Expires': expires(partial)})


@require_POST
def finish(request, partial_id):
    """Once the last piece is in: the quick read, then the form, as for a file sent in one piece."""
    partial = live_or_404(partial_id)
    if not views.uploads_open():
        return views.uploads_closed(request)
    if size_or_404(partial) != partial.length:
        return HttpResponse(status=409)
    try:
        probe.quick_read(partial.file.path)
    except probe.NotAVideo:
        partial.discard()  # Nothing is kept.
        return views.render_upload(request, FileForm(), problem='not_a_video', status=400)

    with transaction.atomic():
        # The original takes over the file, so the partial upload's row goes without it.
        if PartialUpload.objects.filter(pk=partial.pk).delete()[0]:
            # Received now: the form's time limit starts once the last piece is in.
            original = Original.objects.create(file=partial.file.name)
        else:
            # Sent twice: the first one already made the original, and the form is its.
            original = Original.objects.filter(file=partial.file.name, clip__isnull=True).first()
            if original is None:
                raise Http404
    return redirect('clips:confirm', original_id=original.id)


def live_or_404(partial_id):
    """The partial upload, unless it is unknown or its time is up (even before it is deleted)."""
    started_after = timezone.now() - datetime.timedelta(minutes=settings.PARTIAL_UPLOAD_MINUTES)
    partial = PartialUpload.objects.filter(pk=partial_id, started_at__gt=started_after).first()
    if partial is None:
        raise Http404
    return partial


def size_or_404(partial):
    """How much has arrived; a file gone since the row was read was deleted meanwhile."""
    try:
        return partial.file.size
    except FileNotFoundError:
        raise Http404


def expires(partial):
    return http_date((partial.started_at + datetime.timedelta(minutes=settings.PARTIAL_UPLOAD_MINUTES)).timestamp())


def whole_number(header):
    """A header's value as a whole number, or None if it is missing or anything else."""
    # Twenty digits is past any size a file can have, and keeps int() from refusing a long one.
    if header is None or len(header) > 20 or not header.isascii() or not header.isdigit():
        return None
    return int(header)


def tus_response(status, headers=None):
    response = HttpResponse(status=status)
    response['Tus-Resumable'] = VERSION
    response['Cache-Control'] = 'no-store'
    for name, value in (headers or {}).items():
        response[name] = value
    return response
