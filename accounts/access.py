"""Suspending, reinstating and revoking a lawyer (SPEC.md §5).

Suspension removes access at once, and nothing else: the account is deactivated, so
Django refuses the login, its open sessions are deleted, so that reinstatement never
brings one back, and the status says why.
Reinstatement gives the access back. Revocation, after an explicit confirmation, ends the
access for good and closes all the lawyer's cases; it deletes nothing they did. Each
writes an audit entry, in the same transaction.
"""

from django.contrib import messages
from django.contrib.sessions.models import Session
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django_otp import devices_for_user

from audit.models import Action, record
from cases.models import Case

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


@admin_required
def revoke(request, profile_id):
    revocable = [Status.LAWYER, Status.SUSPENDED]
    profile = get_object_or_404(LawyerProfile.objects.select_related('user'), pk=profile_id, status__in=revocable)
    # The explicit confirmation: the admin types the lawyer's email address.
    typed = request.POST.get('email', '').strip().lower()
    confirmed = typed == profile.user.email
    if request.method != 'POST' or not confirmed:
        return render(request, 'accounts/revoke.html', {
            'profile': profile, 'open_cases': profile.cases.open().count(), 'wrong': request.method == 'POST',
        }, status=400 if request.method == 'POST' else 200)
    try:
        with transaction.atomic():
            # Again, locked and with the address checked again: another request may have acted.
            profile = get_object_or_404(
                LawyerProfile.objects.select_for_update().select_related('user'),
                pk=profile_id, status__in=revocable, user__email=typed,
            )
            revoke_locked(request, profile)
    except ValueError:
        raise Http404 from None
    messages.success(request, _("%(name)s's access is revoked.") % {'name': profile.user.get_full_name()})
    return redirect('manage:home')


def revoke_locked(request, profile):
    profile.change_status(Status.REVOKED)
    Case.objects.filter(lawyer_profile=profile).close(Case.ClosedBecause.REVOCATION)
    user = profile.user
    # The address no longer logs in, so that it can be invited again as a new
    # applicant; it is kept, so that the admin is warned first.
    user.former_email, user.email = user.email, ''
    user.set_unusable_password()
    user.is_active = False
    user.save()
    end_sessions(user)
    for device in devices_for_user(user, confirmed=None):
        device.delete()
    record(Action.REVOCATION, profile, admin=request.user)
