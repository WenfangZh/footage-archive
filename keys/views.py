"""Setting up and unlocking a personal key, for applicants and lawyers (design §4, §5.1).

The browser does the cryptography (keys/static/keys/); these views only store and hand back
what it made. The key passphrase and the recovery key never reach the server.
"""

import base64
import secrets

from django.contrib import messages
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_http_methods

from accounts.models import LawyerProfile
from accounts.roles import key_holder_required

from .forms import KeySetupForm
from .models import MEMLIMIT, OPSLIMIT, LawyerKey


def login_id(request):
    """A random id for this login, so that keys a browser tab unlocked under one login are
    never used under another, such as another lawyer's in the same tab (design §4)."""
    return request.session.setdefault('key_login_id', secrets.token_hex(16))


def current_key(profile):
    return LawyerKey.objects.current().filter(lawyer_profile=profile).first()


@key_holder_required
@require_http_methods(['GET', 'POST'])
def setup(request):
    profile = request.user.lawyer_profile
    form = KeySetupForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            # Locked, so that two setups at once cannot both become current.
            LawyerProfile.objects.select_for_update().get(pk=profile.pk)
            existing = current_key(profile)
            if existing is not None and existing.admin_confirmed_at is not None:
                # A key the admin confirmed is replaced only by a key reset (design §5.6).
                return redirect('accounts:key')
            if existing is not None:
                existing.retire()
            LawyerKey.objects.create(lawyer_profile=profile, opslimit=OPSLIMIT, memlimit=MEMLIMIT, **form.cleaned_data)
        messages.success(request, _('Your key is set up. Unlock it once to check your key passphrase.'))
        return redirect('accounts:key')
    existing = current_key(profile)
    if existing is not None and existing.admin_confirmed_at is not None:
        return redirect('accounts:key')
    return render(request, 'keys/setup.html', {
        'form': form, 'replacing': existing is not None, 'opslimit': OPSLIMIT, 'memlimit': MEMLIMIT,
    })


@key_holder_required
@require_GET
def key_page(request):
    key = current_key(request.user.lawyer_profile)
    if key is None:
        return redirect('accounts:key_setup')
    return render(request, 'keys/key.html', {'key': key, 'login_id': login_id(request)})


@key_holder_required
@require_GET
def encrypted_key(request):
    """The lawyer's own current key, as the browser needs it to unlock (design §4).

    Each fetch counts as an unlock, recorded to the day.
    """
    key = current_key(request.user.lawyer_profile)
    if key is None:
        raise Http404
    LawyerKey.objects.filter(pk=key.pk).update(last_unlocked_on=timezone.localdate())
    return JsonResponse({
        'public_key': b64(key.public_key),
        'passphrase_salt': b64(key.passphrase_salt),
        'opslimit': key.opslimit,
        'memlimit': key.memlimit,
        'private_key_under_passphrase': b64(key.private_key_under_passphrase),
    })


def b64(value):
    return base64.b64encode(bytes(value)).decode()
