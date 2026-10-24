"""Setting up and unlocking a personal key, for applicants and lawyers, and handing over the
group key (design §4, §5.1, §5.3).

The browser does the cryptography (keys/static/keys/); these views only store and hand back
what it made. The key passphrase and the recovery key never reach the server, nor does a
private key unless sealed or encrypted.
"""

import base64
import json
import secrets

from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.models import LawyerProfile
from accounts.roles import applicant_required, key_holder_required, lawyer_required

from . import grants
from .forms import GrantForm, GroupKeyForm, KeySetupForm
from .grants import current_key
from .models import IN_USE, MEMLIMIT, OPSLIMIT, KeyGrant, LawyerKey


def login_id(request):
    """A random id for this login, so that keys a browser tab unlocked under one login are
    never used under another, such as another lawyer's in the same tab (design §4)."""
    return request.session.setdefault('key_login_id', secrets.token_hex(16))


# Applicants only: a lawyer replaces their key through a key reset, which the admin confirms
# and the audit log records (design §5.6).
@applicant_required
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
    profile = request.user.lawyer_profile
    key = current_key(profile)
    if key is None and profile.status == LawyerProfile.Status.APPLICANT:
        return redirect('accounts:key_setup')
    return render(request, 'keys/key.html', {'key': key, **unlock_context(request, key)})


def unlock_context(request, key):
    """What a page that unlocks the personal key gives its script (keys/templates/keys/unlock.html)."""
    return {'unlock_public_key': key and b64(key.public_key), 'login_id': login_id(request)}


@key_holder_required
@require_GET
def encrypted_key(request):
    """The person's own current key, as the browser needs it to unlock (design §4)."""
    key = current_key(request.user.lawyer_profile)
    if key is None:
        raise Http404
    return JsonResponse({
        'public_key': b64(key.public_key),
        'passphrase_salt': b64(key.passphrase_salt),
        'opslimit': key.opslimit,
        'memlimit': key.memlimit,
        'private_key_under_passphrase': b64(key.private_key_under_passphrase),
    })


@key_holder_required
@require_POST
def unlocked(request):
    """The browser reports that it unlocked the key and checked it, recorded to the day:
    rotations draw among lawyers who unlocked recently (design §5.5). A fetch alone does not
    count, so a lawyer who has lost their passphrase stops being drawn."""
    LawyerKey.objects.current().filter(lawyer_profile=request.user.lawyer_profile).update(
        last_unlocked_on=timezone.localdate(),
    )
    return HttpResponse(status=204)


def b64(value):
    return base64.b64encode(bytes(value)).decode()


@key_holder_required
@require_GET
def own_grants(request):
    """The grants to the person's own current key on the group keys in use, for their browser
    to open and check, and whether it should make the first group key (design §4, §5.3)."""
    profile = request.user.lawyer_profile
    held = KeyGrant.objects.filter(
        lawyer_key=current_key(profile), group_key__status__in=IN_USE,
    ).select_related('group_key').order_by('pk')
    return JsonResponse({
        'grants': [{
            'id': grant.pk, 'group_public_key': b64(grant.group_key.public_key),
            'sealed_private_key': b64(grant.sealed_private_key), 'checked': grant.checked_at is not None,
        } for grant in held],
        'may_start': grants.may_start(profile),
    })


@key_holder_required
@require_POST
def grant_checked(request):
    """The browser opened a grant to the person's key: it found the group key, or not."""
    grant_id, result = request.POST.get('grant', ''), request.POST.get('result')
    if not grant_id.isdecimal() or result not in ('passed', 'failed'):
        return HttpResponse(status=400)
    try:
        grants.report_check(request.user.lawyer_profile, int(grant_id), result == 'passed')
    except grants.Refused:
        return HttpResponse(status=409)
    return HttpResponse(status=204)


@lawyer_required
@require_POST
def start_group_key(request):
    """The first group key, or a new one once no one is left holding one (design §5.3, §5.7)."""
    form = GroupKeyForm(request.POST)
    if not form.is_valid():
        return HttpResponse(status=400)
    try:
        grants.start(request.user.lawyer_profile, **form.cleaned_data)
    except grants.Refused:
        return HttpResponse(status=409)
    return HttpResponse(status=201)


@lawyer_required
@require_GET
def waiting(request):
    """Admitted colleagues waiting for the group key, for a lawyer who holds it (design §5.3)."""
    profile = request.user.lawyer_profile
    reads = grants.reads_vault(profile)
    return render(request, 'keys/waiting.html', {
        'reads_vault': reads, 'waiting': [key for key in grants.waiting() if key.lawyer_profile != profile] if reads else [],
    })


@lawyer_required
@require_http_methods(['GET', 'POST'])
def grant(request, lawyer_key_id):
    """Hand the group key to a colleague, once their safety number has been compared (design §5.3)."""
    profile = request.user.lawyer_profile
    recipient_key = get_object_or_404(
        LawyerKey.objects.current().select_related('lawyer_profile__user'),
        pk=lawyer_key_id, admin_confirmed_at__isnull=False, lawyer_profile__status=LawyerProfile.Status.LAWYER,
    )
    group_keys = list(grants.missing(recipient_key))
    if recipient_key.lawyer_profile == profile or not group_keys or not grants.reads_vault(profile):
        raise Http404
    form = GrantForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            grants.grant(profile, recipient_key.pk, form.cleaned_data['grants'])
        except grants.Refused:
            messages.error(request, _('The keys changed while this page was open. Please try again.'))
            return redirect('lawyer:grant', lawyer_key_id=recipient_key.pk)
        messages.success(request, _('%(name)s now holds the group key.') % {'name': recipient_key.lawyer_profile.user.get_full_name()})
        return redirect('lawyer:waiting')
    own = current_key(profile)
    return render(request, 'keys/grant.html', {
        'form': form, 'recipient_key': recipient_key, 'recipient_public_key': b64(recipient_key.public_key),
        # The group keys to seal, as JSON in an attribute, which the template escapes.
        'group_keys': json.dumps([{'id': key.pk, 'public_key': b64(key.public_key)} for key in group_keys]),
        **unlock_context(request, own),
    }, status=400 if form.errors else 200)
