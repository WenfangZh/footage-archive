"""Two-factor authentication, mandatory for applicants, lawyers and the admin (SPEC.md §5, §10).

After the password, a person sets up an authenticator app once (setup), then enters a
code from it at every login (verify). Setting it up also gives ten single-use recovery
codes, shown once. There is no page that turns it off or replaces the app: a lost
phone is an account recovery question (SPEC.md §13).

django-otp checks the codes, slows down wrong ones and marks the session as verified;
nothing here checks a code itself. Each code is checked against one device only, under
a lock on its row, so that wrong codes for the app never slow down recovery codes.
"""

import datetime
import re
from base64 import b32encode

import segno
from django import forms
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django_otp import login as otp_login
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

RECOVERY_CODES = 10
# A key shown on the setup page stays valid this long, so that reloading the page does
# not replace the key the person has just scanned.
SETUP_KEY_MINUTES = 15
APP_CODE = re.compile(r'\d{6}')

WRONG_APP_CODE = _('This six-digit code is not right. Check the time on your phone and try again.')
WRONG_RECOVERY_CODE = _('This recovery code is not right, or has been used already.')
TOO_MANY = _('Too many wrong codes. Wait a few minutes before trying again.')


class CodeForm(forms.Form):
    code = forms.CharField(
        label=_('Six-digit code from the app, or recovery code'), max_length=32,
        widget=forms.TextInput(attrs={'autocomplete': 'one-time-code'}),
    )

    def clean_code(self):
        # As typed from paper or a phone: spaces and capitals make no difference.
        return re.sub(r'\s', '', self.cleaned_data['code']).lower()


def has_app(user):
    return TOTPDevice.objects.filter(user=user, confirmed=True).exists()


def required_step(request):
    """Where to send someone logged in with a password but not yet a second factor."""
    step = 'accounts:two_factor_verify' if has_app(request.user) else 'accounts:two_factor_setup'
    return redirect(f"{reverse(step)}?{urlencode({'next': request.get_full_path()})}")


def next_page(request):
    target = request.POST.get('next') or request.GET.get('next', '')
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return reverse('accounts:home')


def shut_out(request):
    # Suspended, revoked and declined accounts, and accounts with no role, see nothing here either.
    from .roles import Role, no_access, role
    return no_access(request) if role(request.user) == Role.NO_ACCESS else None


def checked(device, code):
    """django-otp's verdict on `code` for `device`: True, False, or TOO_MANY while it is slowed down."""
    allowed, _ = device.verify_is_allowed()
    if not allowed:
        return TOO_MANY
    return device.verify_token(code)


@never_cache
@sensitive_post_parameters('code')
@sensitive_variables('codes', 'device', 'form')
@login_required
def setup(request):
    refusal = shut_out(request)
    if refusal or has_app(request.user):
        return refusal or redirect(next_page(request))
    if request.method == 'POST':
        form = CodeForm(request.POST)
        with transaction.atomic():
            device = TOTPDevice.objects.select_for_update().filter(user=request.user, confirmed=False).last()
            if device is None or has_app(request.user):
                # A stale page, or a second click: start again from a fresh page.
                return redirect(f"{reverse('accounts:two_factor_setup')}?{urlencode({'next': next_page(request)})}")
            verdict = checked(device, form.cleaned_data['code']) if form.is_valid() else None
            if verdict is True:
                device.confirmed = True
                device.save()
                TOTPDevice.objects.filter(user=request.user, confirmed=False).delete()
                codes = make_recovery_codes(request.user)
                otp_login(request, device)
                return render(request, 'accounts/recovery_codes.html', {'codes': codes, 'next': next_page(request)})
        if verdict is not None:
            form.add_error('code', TOO_MANY if verdict == TOO_MANY else WRONG_APP_CODE)
    else:
        form = CodeForm()
        device = setup_device(request.user)
    return render(request, 'accounts/two_factor_setup.html', {
        'form': form,
        'qr_code': segno.make(device.config_url).svg_inline(scale=4),
        'key': device_key_text(device),
        'next': next_page(request),
    })


def setup_device(user):
    """The unconfirmed key being set up: a recent one again, or a new one."""
    recent = timezone.now() - datetime.timedelta(minutes=SETUP_KEY_MINUTES)
    device = TOTPDevice.objects.filter(user=user, confirmed=False, created_at__gte=recent).last()
    if device is None:
        TOTPDevice.objects.filter(user=user, confirmed=False).delete()
        device = TOTPDevice.objects.create(user=user, name='authenticator app', confirmed=False)
    return device


def device_key_text(device):
    # The key as authenticator apps take it when typed: base32, in groups of four.
    key = b32encode(device.bin_key).decode().rstrip('=')
    return ' '.join(key[i:i + 4] for i in range(0, len(key), 4))


def make_recovery_codes(user):
    StaticDevice.objects.filter(user=user).delete()
    device = StaticDevice.objects.create(user=user, name='recovery codes')
    codes = [StaticToken.random_token() for _ in range(RECOVERY_CODES)]
    StaticToken.objects.bulk_create(StaticToken(device=device, token=code) for code in codes)
    return codes


@never_cache
@sensitive_post_parameters('code')
@sensitive_variables('device', 'form')
@login_required
def verify(request):
    refusal = shut_out(request)
    if refusal:
        return refusal
    if not has_app(request.user):
        return redirect(f"{reverse('accounts:two_factor_setup')}?{urlencode({'next': next_page(request)})}")
    form = CodeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        code = form.cleaned_data['code']
        is_app_code = bool(APP_CODE.fullmatch(code))
        model = TOTPDevice if is_app_code else StaticDevice
        with transaction.atomic():
            device = model.objects.select_for_update().filter(user=request.user, confirmed=True).first()
            verdict = checked(device, code) if device is not None else False
        if verdict is True:
            otp_login(request, device)
            return redirect(next_page(request))
        form.add_error('code', TOO_MANY if verdict == TOO_MANY else WRONG_APP_CODE if is_app_code else WRONG_RECOVERY_CODE)
    return render(request, 'accounts/two_factor_verify.html', {'form': form, 'next': next_page(request)})
