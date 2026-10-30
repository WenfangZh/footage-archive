"""The admin's tag history of one clip (SPEC.md §7): what lawyers did to its tags, from the audit
log, and when the code holder changed theirs, from the clip's edits. Nothing is kept for it
apart from those two.
"""

from dataclasses import dataclass
from datetime import datetime

from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

from accounts.models import LawyerProfile
from accounts.roles import admin_required
from audit.models import TAG_ACTIONS, AuditEntry
from clips.models import Clip, ClipTag, Edit


@dataclass
class Event:
    at: datetime
    # The audit entry, for lawyers' actions; None for a change by the code holder, which is
    # known only to its 4-hour block (Edit.edited_at).
    entry: AuditEntry | None
    lawyer: LawyerProfile | None
    # None once the tag is deleted: by the code holder, or with the clip.
    tag: ClipTag | None


def events(clip_id):
    entries = AuditEntry.objects.filter(clip_id=clip_id, action__in=TAG_ACTIONS).select_related('lawyer_profile__user')
    tags = ClipTag.objects.in_bulk({entry.clip_tag_id for entry in entries})
    history = [Event(entry.at, entry, entry.lawyer_profile, tags.get(entry.clip_tag_id)) for entry in entries]
    history += [
        Event(edit.edited_at, None, None, None)
        for edit in Edit.objects.filter(clip_id=clip_id, field=Edit.EditableField.TAGS)
    ]
    # An edit's time is the start of its block, so it comes before what lawyers did in that block.
    return sorted(history, key=lambda event: (event.at, event.entry is not None))


@admin_required
@require_GET
def tag_history(request, clip_id):
    history = events(clip_id)
    # A deleted clip's history stays in the audit log.
    if not history and not Clip.objects.filter(pk=clip_id).exists():
        raise Http404
    return render(request, 'cases/tag_history.html', {'clip_id': clip_id, 'events': history})
