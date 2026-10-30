"""The admin's list of withdrawn clips (SPEC.md §7), by identifier only, and their deletion: the
one thing the admin does to a clip. Deleting keeps the clip's hash record and the audit entries,
and the entry it writes names no lawyer, which would single out its reporters.
"""

from django.contrib import messages
from django.db import transaction
from django.db.models import Max
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from accounts.roles import admin_required
from audit.models import Action, AuditEntry, record
from clips.editing import delete_clip
from clips.models import Clip


@admin_required
@require_GET
def withdrawn_clips(request):
    """Each withdrawn clip, latest withdrawal first."""
    withdrawn = set(Clip.objects.filter(status=Clip.Status.WITHDRAWN).values_list('pk', flat=True))
    clips = AuditEntry.objects.filter(action=Action.CLIP_WITHDRAWAL, clip_id__in=withdrawn).values('clip_id').annotate(
        withdrawn_at=Max('at'),
    ).order_by('-withdrawn_at')
    return render(request, 'cases/withdrawn_clips.html', {'clips': clips})


@admin_required
@require_POST
def delete_withdrawn_clip(request, clip_id):
    if request.POST.get('confirm_delete') != 'yes':
        messages.error(request, _('Tick the box to confirm.'))
        return redirect('manage:withdrawn_clips')
    with transaction.atomic():
        clip = Clip.objects.select_for_update().filter(pk=clip_id, status=Clip.Status.WITHDRAWN).first()
        if clip is None:
            # Deleted meanwhile by its code holder, or never withdrawn.
            messages.error(request, _('This clip is no longer here.'))
            return redirect('manage:withdrawn_clips')
        record(Action.WITHDRAWN_CLIP_DELETION, None, admin=request.user, clip=clip)
        delete_clip(clip)
    messages.success(request, _('The clip is deleted. Its hash record is kept.'))
    return redirect('manage:withdrawn_clips')
