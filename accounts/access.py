"""Suspending and reinstating a lawyer (SPEC.md §5).

Suspension removes access at once, and nothing else: the account is deactivated, so
Django refuses the login, its open sessions are deleted, so that reinstatement never
brings one back, and the status says why.
Reinstatement gives the access back. Both write an audit entry, in the same transaction.
"""

from django.contrib import messages
from django.contrib.sessions.models import Session
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from audit.models import Action, record

from .models import LawyerProfile
from .roles import admin_required

Status = LawyerProfile.Status


def change_access(request, profile_id, before, after, active, action, notice):
    try:
        with transaction.atomic():
            profile = get_object_or_404(
                LawyerProfile.objects.select_for_update().select_related('user'), pk=profile_id, status=before,
            )
            profile.change_status(after)
            user = profile.user
            user.is_active = active
            user.save(update_fields=['is_active'])
            if not active:
                end_sessions(user)
            record(action, profile, admin=request.user)
    except ValueError:
        # Another request changed the status first.
        raise Http404 from None
    messages.success(request, notice % {'name': user.get_full_name()})
    return redirect('manage:home')


def end_sessions(user):
    """Delete the account's sessions: refused while it is inactive is not enough."""
    for session in Session.objects.iterator():
        if session.get_decoded().get('_auth_user_id') == str(user.pk):
            session.delete()


@admin_required
@require_POST
def suspend(request, profile_id):
    return change_access(
        request, profile_id, Status.LAWYER, Status.SUSPENDED, False, Action.SUSPENSION,
        _("%(name)s's access is suspended."),
    )


@admin_required
@require_POST
def reinstate(request, profile_id):
    return change_access(
        request, profile_id, Status.SUSPENDED, Status.LAWYER, True, Action.REINSTATEMENT,
        _("%(name)s's access is reinstated."),
    )
