"""A clip's page and its preview, for a lawyer whose case found it (SPEC.md §7).

Only through one of the lawyer's own usable cases, and only while the clip matches it:
there is no other way to a clip. The page shows what the clip says about itself (stated
and metadata values, as uploaded, edits, tags, context) and plays its proxy. Each preview
is logged, but a lawyer watching the same clip in the same case again within two hours is
not logged again: a video player asks for many pieces of one file. Two first requests at
once may both be logged, which only ever logs too much. Opening the page is not a preview.
The evidence copy is downloaded as it is, and every download is logged; its hash record
comes as a JSON file.
"""

import datetime
import json
import os

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_GET

from accounts.roles import lawyer_required
from audit.models import Action, AuditEntry, record
from clips import grid, vault
from clips.models import Clip, HashRecord
from clips.streaming import file_response, requested_range
from clips.tags import DEFAULT_TAGS

from .models import Case
from .search import match_of

PREVIEW_REPEAT = datetime.timedelta(hours=2)


def case_and_clip(request, case_id, clip_id):
    """The lawyer's usable case and a ready clip it matches, and how; or not found."""
    case = get_object_or_404(Case.objects.usable(), pk=case_id, lawyer_profile__user=request.user)
    clip = get_object_or_404(Clip, pk=clip_id, status=Clip.Status.READY)
    match = match_of(case, clip)
    if match is None:
        raise Http404
    return case, clip, match


def place(cell, precision=''):
    """Where a cell is and how wide, for showing; None for no cell."""
    if cell is None:
        return None
    latitude, longitude = grid.centre(cell)
    return {'latitude': latitude, 'longitude': longitude, 'width': round(grid.width_metres(cell), -1), 'precision': precision}


def tag_names(keys, custom):
    return [DEFAULT_TAGS.get(key, key) for key in keys] + list(custom)


@lawyer_required
def clip_page(request, case_id, clip_id):
    case, clip, match = case_and_clip(request, case_id, clip_id)
    mismatch = None
    if clip.stated_time and clip.metadata_time:
        gap = abs(clip.stated_time - clip.metadata_time)
        if gap > datetime.timedelta(minutes=settings.TIME_MISMATCH_MINUTES):
            mismatch = round(gap.total_seconds() / 60)
    tags = clip.tags.order_by('source', 'key', 'custom_name')
    uploaded = clip.as_uploaded_tags or {}
    return render(request, 'cases/clip.html', {
        'case': case, 'clip': clip, 'match': match, 'mismatch': mismatch,
        'stated_place': place(clip.stated_cell, clip.get_stated_precision_display()),
        'metadata_place': place(clip.metadata_cell),
        'uploaded_place': place(clip.as_uploaded_stated_cell, clip.get_as_uploaded_stated_precision_display()),
        'tags': [(tag.get_key_display() or tag.custom_name, tag.get_source_display()) for tag in tags],
        'uploaded_tags': tag_names(uploaded.get('default', []), uploaded.get('custom', [])),
        'edits': clip.edits.order_by('edited_at', 'pk'),
        'context': vault.get_text(clip, vault.CONTEXT),
        'has_proxy': vault.has_file(clip, vault.PROXY),
    })


def open_or_404(clip, kind):
    """The clip's vault file, opened, and its size read from that same open file."""
    try:
        opened = vault.open_file(clip, kind)
    except (FileNotFoundError, ObjectDoesNotExist):
        raise Http404 from None
    return opened, os.fstat(opened.fileno()).st_size


@lawyer_required
@require_GET
def preview(request, case_id, clip_id):
    case, clip, _ = case_and_clip(request, case_id, clip_id)
    proxy, size = open_or_404(clip, vault.PROXY)
    try:
        recently = AuditEntry.objects.filter(
            action=Action.PREVIEW, case=case, clip_id=clip.pk, at__gte=timezone.now() - PREVIEW_REPEAT,
        )
        if not recently.exists():
            record(Action.PREVIEW, case.lawyer_profile, case=case, clip=clip)
        return file_response(request, proxy, size, 'video/mp4')
    except BaseException:
        proxy.close()
        raise


# The evidence copy keeps the original's container (clips.evidence).
CONTENT_TYPES = {'mp4': 'video/mp4', 'mov': 'video/quicktime', 'mkv': 'video/x-matroska'}


# A download resumed within this time asks only for the rest of the file: the same download.
DOWNLOAD_REPEAT = datetime.timedelta(hours=2)


@lawyer_required
@require_GET
def download(request, case_id, clip_id):
    case, clip, _ = case_and_clip(request, case_id, clip_id)
    evidence, size = open_or_404(clip, vault.EVIDENCE_COPY)
    try:
        extension = vault.extension(clip, vault.EVIDENCE_COPY)
        served, _ = requested_range(request, size)
        recently = AuditEntry.objects.filter(
            action=Action.DOWNLOAD, case=case, clip_id=clip.pk, at__gte=timezone.now() - DOWNLOAD_REPEAT,
        )
        # Logged whenever it starts from the first byte, and otherwise unless it carries on a
        # download logged within two hours: no part of the file is served unlogged.
        if served is not None and (served[0] == 0 or not recently.exists()):
            record(Action.DOWNLOAD, case.lawyer_profile, case=case, clip=clip)
        response = file_response(request, evidence, size, CONTENT_TYPES.get(extension, 'application/octet-stream'))
    except BaseException:
        evidence.close()
        raise
    response['Content-Disposition'] = content_disposition_header(True, f'clip-{clip.pk}.{extension}')
    return response


def hash_record_data(record_):
    """A hash record as plain data: the fingerprints of the original and the evidence copy."""
    def hashes(sha256, streams, hashed_at):
        return {
            'sha256': sha256,
            'streams': [{'kind': {'v': 'video', 'a': 'audio'}[kind], 'sha256': digest} for kind, digest in streams],
            # Kept only to its 4-hour block (SPEC.md §6).
            'hashed_in_4_hour_block_from': hashed_at.isoformat(),
        }
    return {
        'hash_record': str(record_.pk),
        'clip': str(record_.clip_id),
        'original': hashes(record_.original_sha256, record_.original_stream_hashes, record_.original_hashed_at),
        'evidence_copy': hashes(record_.evidence_sha256, record_.evidence_stream_hashes, record_.evidence_hashed_at),
    }


@lawyer_required
@require_GET
def hash_record(request, case_id, clip_id):
    _, clip, _ = case_and_clip(request, case_id, clip_id)
    stored = HashRecord.objects.filter(clip=clip).first()
    if stored is None:
        raise Http404
    response = HttpResponse(json.dumps(hash_record_data(stored), indent=2), content_type='application/json')
    response['Content-Disposition'] = content_disposition_header(True, f'clip-{clip.pk}-hash-record.json')
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'no-store, private'
    return response
