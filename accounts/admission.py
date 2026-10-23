"""Admitting or declining an applicant (SPEC.md §5).

Admission records how the person was verified with their bar and who vouched for them,
then makes them a lawyer; only once they have set a password, a second factor and a
personal key, since an invitation link works only for applicants (SPEC.md §5), and only
once the admin has compared that key's safety number with them (docs/m5-encryption-design.md §5.2). Declining, after a confirmation, erases their name, bar and
email address, so that the address can be invited again as a new applicant; the account
stays only as an identifier for the audit log. Both write an audit entry.
"""

from django import forms
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_otp import devices_for_user
from django_otp.plugins.otp_totp.models import TOTPDevice

from audit.models import Action, record
from keys.models import LawyerKey

from .models import LawyerProfile
from .roles import admin_required


class AdmissionForm(forms.ModelForm):
    class Meta:
        model = LawyerProfile
        fields = ['verified_via', 'vouched_by']
        labels = {
            'verified_via': _('How their registration with the bar was verified'),
            'vouched_by': _('Who vouched for them'),
        }
        help_texts = {
            'verified_via': _("Through the bar directory's contact details, never those the person gave."),
            'vouched_by': _('A trusted defence organisation or an admitted lawyer.'),
        }

    # The key whose safety number the page showed: one made since was not the one compared.
    key = forms.IntegerField(widget=forms.HiddenInput)
    key_confirmed = forms.BooleanField(
        label=_("I compared this safety number with them, through the bar directory's contact details."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = True


def applicant_or_404(profile_id, lock=False):
    profiles = LawyerProfile.objects.select_related('user')
    if lock:
        profiles = profiles.select_for_update()
    return get_object_or_404(profiles, pk=profile_id, status=LawyerProfile.Status.APPLICANT)


def ready(user):
    """Whether the person has set their password, their second factor and their personal key."""
    return (
        user.has_usable_password()
        and TOTPDevice.objects.filter(user=user, confirmed=True).exists()
        and current_key(user) is not None
    )


def current_key(user):
    return LawyerKey.objects.current().filter(lawyer_profile__user=user).first()


@admin_required
def applicant(request, profile_id):
    profile = applicant_or_404(profile_id)
    key = current_key(profile.user)
    form = AdmissionForm(
        request.POST if request.method == 'POST' else None, instance=profile, initial={'key': key and key.pk},
    )
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                # Again, locked: another admin may have acted since the page was shown.
                profile = applicant_or_404(profile_id, lock=True)
                if not ready(profile.user):
                    raise NotReady
                key = current_key(profile.user)
                if key.pk != form.cleaned_data['key']:
                    raise KeyChanged
                key.admin_confirmed_at = timezone.now()
                key.save(update_fields=['admin_confirmed_at'])
                profile.verified_via = form.cleaned_data['verified_via']
                profile.vouched_by = form.cleaned_data['vouched_by']
                profile.save()
                profile.change_status(LawyerProfile.Status.LAWYER)
                record(Action.ADMISSION, profile, admin=request.user)
        except NotReady:
            form.add_error(None, _('They can be admitted once they have set their password, two-factor authentication and personal key.'))
        except KeyChanged:
            form.add_error(None, _('They have made a new key since this page was shown. Compare its safety number with them again.'))
        except (ValueError, IntegrityError):
            raise Http404 from None
        else:
            messages.success(request, _('%(name)s is admitted.') % {'name': profile.user.get_full_name()})
            return redirect('manage:home')
    return render(request, 'accounts/applicant_review.html', {
        'profile': profile, 'form': form, 'ready': ready(profile.user), 'key': key,
    })


class NotReady(Exception):
    pass


class KeyChanged(Exception):
    pass


@admin_required
def decline(request, profile_id):
    profile = applicant_or_404(profile_id)
    if request.method == 'POST' and request.POST.get('confirm') == 'yes':
        with transaction.atomic():
            profile = applicant_or_404(profile_id, lock=True)
            user = profile.user
            # Erased while still an applicant: a declined profile can never change again.
            profile.bar = ''
            profile.save()
            user.first_name = user.last_name = ''
            user.email = ''
            user.set_unusable_password()
            # Inactive: Django refuses the login and ends any open session.
            user.is_active = False
            user.save()
            for device in devices_for_user(user, confirmed=None):
                device.delete()
            profile.change_status(LawyerProfile.Status.DECLINED)
            record(Action.DECLINING, profile, admin=request.user)
        return redirect('manage:home')
    return render(request, 'accounts/decline.html', {'profile': profile}, status=400 if request.method == 'POST' else 200)
