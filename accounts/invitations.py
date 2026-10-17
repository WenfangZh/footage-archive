"""Invitations (SPEC.md §5): the admin invites a person by email; they become an applicant.

The link in the email is Django's own set-password link, the one behind its password
reset: its token is tied to the account's password, so it stops working once the person
sets one. A new link gives the account a new unusable password first, so earlier links
stop working too; it can only be sent before the person has set a password, so that it
never wipes one. Links expire after INVITATION_DAYS. They work only for applicants.
"""

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth import views as auth_views
from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.contrib.auth.tokens import default_token_generator
from django.contrib import messages
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import translation
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from audit.models import Action, record

from .models import LawyerProfile
from .roles import admin_required

User = get_user_model()


class InvitationForm(forms.Form):
    email = forms.EmailField(
        label=_('Email address'), max_length=254, help_text=_("The person's own professional address."),
    )
    first_name = forms.CharField(label=_('First name'), max_length=150)
    last_name = forms.CharField(label=_('Last name'), max_length=150)
    bar = forms.CharField(label=_('Bar'), max_length=100)
    # Shown only when needed: a revoked lawyer's address may be invited again, knowingly.
    # Ticking it confirms that one address only, so it cannot carry over to another.
    invite_former_address = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data['email'])
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError(_('An account already uses this address.'))
        return email

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get('email')
        confirmed = cleaned.get('invite_former_address') == email
        if email and not confirmed and User.objects.filter(former_email=email).exists():
            field = self.fields['invite_former_address']
            field.label = _('Invite this address anyway')
            field.widget = forms.CheckboxInput(attrs={'value': email}, check_test=lambda value: False)
            self.add_error('invite_former_address', _(
                'This address belonged to a lawyer whose access was revoked. Tick the box to invite it again, '
                'as a new applicant who needs verifying and a vouch like anyone else.'
            ))
        return cleaned


@admin_required
def invite(request):
    form = InvitationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                # No usable password until the person sets one from the link.
                user = User.objects.create_user(
                    form.cleaned_data['email'],
                    first_name=form.cleaned_data['first_name'], last_name=form.cleaned_data['last_name'],
                )
                profile = LawyerProfile.objects.create(user=user, bar=form.cleaned_data['bar'])
                record(Action.INVITATION, profile, admin=request.user)
                # Inside the transaction: if the email cannot be sent, no account is kept.
                send_invitation(request, user)
        except IntegrityError:
            # Another invitation to the same address got there first.
            form.add_error('email', _('An account already uses this address.'))
        else:
            return sent(request, user)
    return render(request, 'accounts/invite.html', {'form': form})


def sent(request, user):
    # Back to a page that a reload does not send again.
    messages.success(request, _('An invitation link went to %(email)s.') % {'email': user.email})
    return redirect('manage:home')


@admin_required
@require_POST
def send_new_link(request, profile_id):
    with transaction.atomic():
        profile = get_object_or_404(
            LawyerProfile.objects.select_for_update().select_related('user'),
            pk=profile_id, status=LawyerProfile.Status.APPLICANT, 
            # Not once they have set a password: a new link would wipe it.
            user__password__startswith=UNUSABLE_PASSWORD_PREFIX,
        )
        user = profile.user
        # A new unusable password: every earlier link stops working.
        user.set_unusable_password()
        user.save(update_fields=['password'])
        record(Action.INVITATION, profile, admin=request.user)
        send_invitation(request, user)
    return sent(request, user)


def send_invitation(request, user):
    link = request.build_absolute_uri(reverse('accounts:accept_invitation', kwargs={
        'uidb64': urlsafe_base64_encode(force_bytes(user.pk)),
        'token': default_token_generator.make_token(user),
    }))
    # In the archive's default language: the admin's choice says nothing about the person's.
    with translation.override(settings.LANGUAGE_CODE):
        subject = str(_('Your invitation to the footage archive'))
        body = render_to_string('accounts/invitation_email.txt', {'link': link, 'days': settings.INVITATION_DAYS})
    send_mail(subject, body, None, [user.email])


class AcceptInvitationView(auth_views.PasswordResetConfirmView):
    """Django's set-password page, for applicants only; then on to the second factor."""

    template_name = 'accounts/accept_invitation.html'
    post_reset_login = True
    success_url = reverse_lazy('accounts:home')

    def get_user(self, uidb64):
        user = super().get_user(uidb64)
        if user is None or not LawyerProfile.objects.filter(
            user=user, status=LawyerProfile.Status.APPLICANT,
        ).exists():
            return None
        return user


accept_invitation = AcceptInvitationView.as_view()
