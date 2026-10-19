"""A clip's page and its preview, for a lawyer whose case found it (SPEC.md §7).

Only through one of the lawyer's own usable cases, and only while the clip matches it:
there is no other way to a clip. The page shows what the clip says about itself (stated
and metadata values, as uploaded, edits, tags, context) and plays its proxy. Each preview
is logged, but a lawyer watching the same clip in the same case again within two hours is
not logged again: a video player asks for many pieces of one file.
"""

import datetime

from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from accounts.roles import lawyer_required
from audit.models import Action, AuditEntry, record
from clips import grid, vault
from clips.models import Clip
from clips.streaming import file_response
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
        if gap >= datetime.timedelta(minutes=settings.TIME_MISMATCH_MINUTES):
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


@lawyer_required
def preview(request, case_id, clip_id):
    case, clip, _ = case_and_clip(request, case_id, clip_id)
    if not vault.has_file(clip, vault.PROXY):
        raise Http404
    recently = AuditEntry.objects.filter(
        action=Action.PREVIEW, case=case, clip_id=clip.pk, at__gte=timezone.now() - PREVIEW_REPEAT,
    )
    if not recently.exists():
        record(Action.PREVIEW, case.lawyer_profile, case=case, clip=clip)
    proxy = vault.open_file(clip, vault.PROXY)
    return file_response(request, proxy, proxy.size, 'video/mp4')
