"""A clip's page and its preview, for a lawyer whose case found it (SPEC.md §7).

Only through one of the lawyer's own usable cases, and only while the clip matches it:
there is no other way to a clip. The page shows what the clip says about itself (stated
and metadata values, as uploaded, edits, tags, context, contact details) and plays its proxy. Each preview
is logged, but a lawyer watching the same clip in the same case again within two hours is
not logged again: a video player asks for many pieces of one file. Two first requests at
once may both be logged, which only ever logs too much. Opening the page is logged the
same way, as a view.
The evidence copy is downloaded as it is, and every download is logged; its hash record
comes as a JSON file. The vault's content (preview, evidence copy, context and contact
details) goes only to a lawyer who holds the group key (docs/m5-encryption-design.md §3.3);
without it, the page shows the clip's index data alone. The preview and the download
decrypt their file with its data key, which the page's script opens with the group key
and hands over first (§6.3): it comes back in a cookie that only that clip's preview and
download in that case receive, and is never stored on the server.
"""

import base64
import binascii
import datetime
import json

from django.conf import settings
from django import forms
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST

from accounts.roles import lawyer_required
from alerts.checks import check_activity
from alerts.models import AlertLimitRaise
from audit.models import Action, AuditEntry, record
from clips import grid, vault
from clips.models import Clip, HashRecord
from clips.streaming import file_response, requested_range
from clips.tags import DEFAULT_TAGS
from keys.forms import Base64BytesField
from keys.grants import current_key, reads_vault
from keys.views import unlock_context

from .models import Case
from .search import match_of

PREVIEW_REPEAT = datetime.timedelta(hours=2)
# How long a data key handed over stays usable: as long as the preview and download logging windows.
DATA_KEY_LIFETIME = datetime.timedelta(hours=2)
# The files whose data key a page hands over, by the name in their URL, with their cookie.
DATA_KEY_FILES = {
    'proxy': (vault.PROXY, 'proxy_data_key'),
    'evidence-copy': (vault.EVIDENCE_COPY, 'evidence_copy_data_key'),
}
COOKIES = dict(DATA_KEY_FILES.values())


def case_and_clip(request, case_id, clip_id):
    """The lawyer's usable case and a ready clip it matches, and how; or not found."""
    case = get_object_or_404(Case.objects.usable(), pk=case_id, lawyer_profile__user=request.user)
    clip = get_object_or_404(Clip, pk=clip_id, status=Clip.Status.READY)
    match = match_of(case, clip)
    if match is None:
        raise Http404
    return case, clip, match


def vault_case_and_clip(request, case_id, clip_id):
    """As case_and_clip, for a lawyer who holds the group key; otherwise not found."""
    found = case_and_clip(request, case_id, clip_id)
    if not reads_vault(request.user.lawyer_profile):
        raise Http404
    return found


def place(cell, precision=''):
    """Where a cell is and how wide, for showing; None for no cell."""
    if cell is None:
        return None
    latitude, longitude = grid.centre(cell)
    return {'latitude': latitude, 'longitude': longitude, 'width': round(grid.width_metres(cell), -1), 'precision': precision}


def tag_names(keys, custom):
    return [DEFAULT_TAGS.get(key, key) for key in keys] + list(custom)


def record_once_in_a_while(action, case, clip):
    """Log `action` on the clip in the case, unless it was logged within PREVIEW_REPEAT."""
    recently = AuditEntry.objects.filter(
        lawyer_profile=case.lawyer_profile, action=action, case=case, clip_id=clip.pk,
        at__gte=timezone.now() - PREVIEW_REPEAT,
    )
    if not recently.exists():
        record(action, case.lawyer_profile, case=case, clip=clip)


@lawyer_required
@require_GET
def clip_page(request, case_id, clip_id):
    case, clip, match = case_and_clip(request, case_id, clip_id)
    # What the page shows (context, tags, times and places) is access too: logged, but
    # not again within two hours, like the preview.
    record_once_in_a_while(Action.CLIP_VIEW, case, clip)
    mismatch = None
    if clip.stated_time and clip.metadata_time:
        gap = abs(clip.stated_time - clip.metadata_time)
        if gap > datetime.timedelta(minutes=settings.TIME_MISMATCH_MINUTES):
            mismatch = round(gap.total_seconds() / 60)
    tags = clip.tags.order_by('source', 'key', 'custom_name')
    reads = reads_vault(case.lawyer_profile)
    uploaded = clip.as_uploaded_tags or {}
    return render(request, 'cases/clip.html', {
        'case': case, 'clip': clip, 'match': match, 'mismatch': mismatch,
        'stated_place': place(clip.stated_cell, clip.get_stated_precision_display()),
        'metadata_place': place(clip.metadata_cell),
        'uploaded_place': place(clip.as_uploaded_stated_cell, clip.get_as_uploaded_stated_precision_display()),
        'tags': [(tag.get_key_display() or tag.custom_name, tag.get_source_display()) for tag in tags],
        'uploaded_tags': tag_names(uploaded.get('default', []), uploaded.get('custom', [])),
        'edits': clip.edits.order_by('edited_at', 'pk'),
        'reads_vault': reads,
        'context': reads and vault.get_text(clip, vault.CONTEXT),
        # Left by the code holder on purpose, despite the risk, so that a lawyer can reach them.
        'contact_details': reads and vault.get_text(clip, vault.CONTACT_DETAILS),
        'has_proxy': reads and vault.has_file(clip, vault.PROXY),
        # For the script to open with the group key (keys/static/keys/clip_keys.js), as JSON in
        # attributes, which the template escapes.
        'proxy_keys': reads and sealed_keys_json(clip, vault.PROXY),
        'evidence_keys': reads and sealed_keys_json(clip, vault.EVIDENCE_COPY),
        **unlock_context(request, current_key(case.lawyer_profile)),
    })


def sealed_keys_json(clip, kind):
    return json.dumps([{
        'group_public_key': base64.b64encode(bytes(sealed.group_key.public_key)).decode(),
        'sealed_data_key': base64.b64encode(bytes(sealed.sealed_data_key)).decode(),
    } for sealed in vault.sealed_keys(clip, kind)])


@lawyer_required
@sensitive_post_parameters('data_key')
@require_POST
def data_key(request, case_id, clip_id, file):
    """Take a file's data key from the page's script, check it, and hand it back in a cookie
    for the preview or download (design §6.3). Nothing is stored."""
    if file not in DATA_KEY_FILES:
        raise Http404
    case, clip, _ = vault_case_and_clip(request, case_id, clip_id)
    kind, cookie = DATA_KEY_FILES[file]
    try:
        key = Base64BytesField(vault.DATA_KEY_BYTES).clean(request.POST.get('data_key', ''))
    except forms.ValidationError:
        return HttpResponse(status=400)
    if not vault.is_data_key(clip, kind, key):
        return HttpResponseForbidden()
    response = HttpResponse(status=204)
    response.set_cookie(
        cookie, cookie_value(key),
        max_age=int(DATA_KEY_LIFETIME.total_seconds()),
        # Sent only with this clip's pages in this case: its preview and its download.
        path=reverse('lawyer:clip', kwargs={'case_id': case.pk, 'clip_id': clip.pk}),
        secure=True, httponly=True, samesite='Strict',
    )
    return response


def cookie_value(data_key):
    # URL-safe and unpadded: characters a cookie holds without quoting.
    return base64.urlsafe_b64encode(data_key).decode().rstrip('=')


def open_or_refuse(request, clip, kind):
    """The clip's vault file, opened with the data key in its cookie; or a refusal, if the
    key is missing or wrong (design §6.3)."""
    value = request.COOKIES.get(COOKIES[kind], '')
    try:
        key = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
    except (binascii.Error, ValueError):
        key = None
    if key is not None and len(key) != vault.DATA_KEY_BYTES:
        key = None
    try:
        return vault.open_file(clip, kind, key), None
    except vault.WrongKey:
        return None, HttpResponseForbidden()
    except (FileNotFoundError, ObjectDoesNotExist, vault.Unreadable):
        raise Http404 from None


@lawyer_required
@require_GET
def preview(request, case_id, clip_id):
    case, clip, _ = vault_case_and_clip(request, case_id, clip_id)
    proxy, refusal = open_or_refuse(request, clip, vault.PROXY)
    if refusal:
        return refusal
    try:
        record_once_in_a_while(Action.PREVIEW, case, clip)
        return file_response(request, proxy, proxy.size, 'video/mp4')
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
    case, clip, _ = vault_case_and_clip(request, case_id, clip_id)
    evidence, refusal = open_or_refuse(request, clip, vault.EVIDENCE_COPY)
    if refusal:
        return refusal
    size = evidence.size
    try:
        extension = vault.extension(clip, vault.EVIDENCE_COPY)
        served, _ = requested_range(request, size)
        recently = AuditEntry.objects.filter(
            lawyer_profile=case.lawyer_profile, action=Action.DOWNLOAD, case=case, clip_id=clip.pk,
            at__gte=timezone.now() - DOWNLOAD_REPEAT,
        )
        # Logged whenever it starts from the first byte, and otherwise unless it carries on a
        # download logged within two hours: no part of the file is served unlogged.
        if served is not None and (served[0] == 0 or not recently.exists()):
            record(Action.DOWNLOAD, case.lawyer_profile, case=case, clip=clip)
            check_activity(case.lawyer_profile, AlertLimitRaise.Activity.DOWNLOADS)
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
