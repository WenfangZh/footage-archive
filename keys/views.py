"""Setting up and unlocking a personal key, for applicants and lawyers, and handing over the
group key (design §4, §5.1, §5.3).

The browser does the cryptography (keys/static/keys/); these views only store and hand back
what it made. The key passphrase and the recovery key never reach the server, nor does a
private key unless sealed or encrypted.
"""

import base64
import functools
import json
import secrets

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.models import LawyerProfile
from accounts.roles import admin_required, key_holder_required, lawyer_required
from alerts.checks import failed_login
from clips import rate_limits

from . import grants, recovery, rotation
from .forms import (
    Base64BytesField, CheckResultForm, ExposureReportForm, GrantForm, GroupKeyForm, ItemIdsForm, KeyConfirmationForm,
    KeyResetForm, KeySetupForm, NewGroupKeyForm, NewPassphraseForm, ResealedForm,
)
from .grants import current_key
from .models import IN_USE, MEMLIMIT, OPSLIMIT, PUBLIC_KEY_BYTES, KeyGrant, LawyerKey, RotationCheck


def login_id(request):
    """A random id for this login, so that keys a browser tab unlocked under one login are
    never used under another, such as another lawyer's in the same tab (design §4)."""
    return request.session.setdefault('key_login_id', secrets.token_hex(16))


# An applicant before admission, or a lawyer without a key, after a key reset (design §5.6).
# An applicant may replace a key the admin has not confirmed; a lawyer's key, confirmed or not,
# is replaced only through a key reset, which the audit log records, since case labels may be
# sealed to it.
@key_holder_required
@require_http_methods(['GET', 'POST'])
def setup(request):
    profile = request.user.lawyer_profile
    form = KeySetupForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            # Locked, so that two setups at once cannot both become current.
            profile = LawyerProfile.objects.select_for_update().get(pk=profile.pk)
            existing = current_key(profile)
            if existing is not None and not replaceable(existing):
                return redirect('accounts:key')
            if existing is not None:
                existing.retire()
            LawyerKey.objects.create(lawyer_profile=profile, opslimit=OPSLIMIT, memlimit=MEMLIMIT, **form.cleaned_data)
        messages.success(request, _('Your key is set up. Unlock it once to check your key passphrase.'))
        return redirect('accounts:key')
    existing = current_key(profile)
    if existing is not None and not replaceable(existing):
        return redirect('accounts:key')
    return render(request, 'keys/setup.html', {
        'form': form, 'replacing': existing is not None, 'opslimit': OPSLIMIT, 'memlimit': MEMLIMIT,
    })


def replaceable(key):
    """Whether setting up a new key may retire `key`: only an applicant's, before the admin
    confirmed it."""
    return key.admin_confirmed_at is None and key.lawyer_profile.status == LawyerProfile.Status.APPLICANT


@key_holder_required
@require_GET
def key_page(request):
    profile = request.user.lawyer_profile
    key = current_key(profile)
    if key is None:
        return redirect('accounts:key_setup')
    return render(request, 'keys/key.html', {
        'key': key, 'lawyer': profile.status == LawyerProfile.Status.LAWYER, **unlock_context(request, key),
    })


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


@key_holder_required
@require_http_methods(['GET', 'POST'])
def recover(request):
    """A forgotten key passphrase: the browser opens the key with the recovery key, and sets a
    new passphrase (design §5.6). The key, its safety number and its grants stay the same."""
    profile = request.user.lawyer_profile
    key = current_key(profile)
    if key is None:
        return redirect('accounts:key_setup')
    form = NewPassphraseForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            recovery.replace_passphrase(profile, **form.cleaned_data)
        except recovery.Refused:
            messages.error(request, _('Your key changed while this page was open. Please try again.'))
            return redirect('accounts:key_recover')
        messages.success(request, _('Your new key passphrase is saved. Your key and its safety number stay the same.'))
        return redirect('accounts:key')
    return render(request, 'keys/recover.html', {
        # The copy the recovery key opens: on this page only, never in what every page fetches.
        'recovery_copy': b64(key.private_key_under_recovery_key),
        'form': form, 'opslimit': OPSLIMIT, 'memlimit': MEMLIMIT, **unlock_context(request, key),
    }, status=400 if form.errors else 200)


def password_checked(request, form):
    """Whether `form`, which asks for the login password again, is valid. Each attempt counts as
    a failed login until it succeeds, under the login's own limits and alert (accounts.views),
    so that a session left open cannot be used to guess the password. Raises RateLimited when
    over a limit, before the password is checked."""
    email = request.user.email
    counted = [(rate_limits.FAILED_LOGINS, None), (rate_limits.FAILED_LOGINS_FOR_ACCOUNT, email)]
    if any(rate_limits.count(request, action, account=account) > rate_limits.limit(action) for action, account in counted):
        raise RateLimited
    valid = form.is_valid()
    if valid or not form.has_error('password'):
        for action, account in counted:
            rate_limits.uncount(request, action, account=account)
    else:
        failed_login(request, email)
    return valid


class RateLimited(Exception):
    pass


@lawyer_required
@sensitive_post_parameters('password')
@require_http_methods(['GET', 'POST'])
def reset(request):
    """Both the key passphrase and the recovery key lost (design §5.6): the key is retired, a
    rotation is requested, and the lawyer sets up a new key."""
    profile = request.user.lawyer_profile
    key = current_key(profile)
    if key is None:
        return redirect('accounts:key_setup')
    form = KeyResetForm(request.user, request.POST if request.method == 'POST' else None, initial={'key': key.pk})
    try:
        confirmed = request.method == 'POST' and password_checked(request, form)
    except RateLimited:
        return rate_limits.too_many(request, 'accounts/login_limited.html')
    if confirmed:
        try:
            recovery.reset(profile, form.cleaned_data['key'])
        except recovery.Refused:
            messages.error(request, _('Your key changed while this page was open. Please try again.'))
            return redirect('accounts:key')
        messages.success(request, _('Your key is reset. Set up a new one.'))
        return redirect('accounts:key_setup')
    return render(request, 'keys/reset.html', {
        'form': form, 'last_holder': grants.last_holder(profile),
    }, status=400 if form.errors else 200)


@lawyer_required
@sensitive_post_parameters('password')
@require_http_methods(['GET', 'POST'])
def exposure(request):
    """A key passphrase or recovery key that may have been seen (design §5.6): the browser makes
    a new key and reseals the lawyer's labels and messages to it, then sends them here, by fetch,
    so that it can try again when a case made or a message sent meanwhile changed them."""
    profile = request.user.lawyer_profile
    key = current_key(profile)
    if key is None:
        return redirect('accounts:key_setup')
    if request.method == 'GET':
        return render(request, 'keys/exposure.html', {
            'reads_vault': grants.reads_vault(profile), 'another_can_grant': grants.another_can_grant(profile),
            'opslimit': OPSLIMIT, 'memlimit': MEMLIMIT, **unlock_context(request, key),
        })
    form = ExposureReportForm(request.user, request.POST)
    try:
        if not password_checked(request, form):
            return JsonResponse({'wrong_password': form.has_error('password')}, status=400)
    except RateLimited:
        return rate_limits.too_many(request, 'accounts/login_limited.html')
    data = form.cleaned_data
    new_key = {name: data[name] for name in KeySetupForm.base_fields}
    try:
        recovery.report_exposure(
            profile, data['old_public_key'], new_key, (data['resealed_labels'], data['unreadable_labels']),
            (data['resealed_messages'], data['unreadable_messages']),
        )
    except recovery.SealedChanged:
        return HttpResponse(status=409)
    except recovery.Refused as refusal:
        return JsonResponse({'last_holder': isinstance(refusal, recovery.LastHolder)}, status=403)
    messages.success(request, _(
        'Your new key is set up, and a rotation of the group key is requested. The admin will compare '
        'its safety number with you, then a lawyer will give you the group key again.'
    ))
    if data['unreadable_labels']:
        lost = len(data['unreadable_labels'])
        messages.warning(request, ngettext(
            'The label of %(count)d case could not be opened, so could not be moved to your new key: it is lost.',
            'The labels of %(count)d cases could not be opened, so could not be moved to your new key: they are lost.',
            lost,
        ) % {'count': lost})
    response = HttpResponse(status=201)
    # The old key, unlocked in this browser's tabs, is retired: forget it, as at logout.
    response['Clear-Site-Data'] = '"storage"'
    return response


@lawyer_required
@require_GET
def own_sealed(request):
    """The labels and messages sealed to the lawyer's current key, for their browser to reseal
    (design §5.6, §7.3)."""
    key = current_key(request.user.lawyer_profile)
    if key is None:
        raise Http404
    from cases import labels  # Here: cases.labels imports this module.
    from clips import vault  # Here: clips.vault imports keys.grants.
    return JsonResponse({'public_key': b64(key.public_key), **{
        name: [
            {'id': item['id'], 'sealed_key': b64(item['sealed_key']), 'key_check': b64(item['key_check'])}
            for item in items
        ]
        for name, items in [('labels', labels.sealed_to(key)), ('messages', vault.messages_sealed_to(key))]
    }})


@admin_required
@require_http_methods(['GET', 'POST'])
def confirm_key(request, profile_id):
    """The admin compares the safety number of a lawyer's new key with them (design §5.2, §5.6)."""
    key = recovery.unconfirmed_key(profile_id)
    if key is None:
        raise Http404
    form = KeyConfirmationForm(request.POST if request.method == 'POST' else None, initial={'key': key.pk})
    if request.method == 'POST' and form.is_valid():
        try:
            recovery.confirm(request.user, profile_id, form.cleaned_data['key'])
        except recovery.Refused:
            messages.error(request, _('They have made a new key since this page was shown. Compare its safety number with them again.'))
            return redirect('manage:confirm_key', profile_id=profile_id)
        messages.success(request, _("%(name)s's new key is confirmed. A lawyer can now give them the group key.") % {
            'name': key.lawyer_profile.user.get_full_name(),
        })
        return redirect('manage:home')
    return render(request, 'keys/confirm_key.html', {'form': form, 'key': key}, status=400 if form.errors else 200)


def b64(value):
    return base64.b64encode(bytes(value)).decode()


@key_holder_required
@require_GET
def own_grants(request):
    """The grants to the person's own current key on the group keys in use, for their browser
    to open and check, and whether it should make the first group key (design §4, §5.3)."""
    profile = request.user.lawyer_profile
    key = current_key(profile)
    held = KeyGrant.objects.filter(
        lawyer_key=key, group_key__status__in=IN_USE,
    ).select_related('group_key').order_by('pk')
    return JsonResponse({
        # The key these grants are to: the browser opens them only with that key's private half.
        'public_key': b64(key.public_key) if key else None,
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
    try:
        public_key = Base64BytesField(PUBLIC_KEY_BYTES).clean(request.POST.get('public_key', ''))
    except ValidationError:
        public_key = None
    if not (grant_id.isascii() and grant_id.isdigit()) or result not in ('passed', 'failed') or public_key is None:
        return HttpResponse(status=400)
    try:
        grants.report_check(request.user.lawyer_profile, int(grant_id), public_key, result == 'passed')
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
            return redirect('lawyer:waiting')
        messages.success(request, _('%(name)s now holds the group key.') % {'name': recipient_key.lawyer_profile.user.get_full_name()})
        return redirect('lawyer:waiting')
    own = current_key(profile)
    return render(request, 'keys/grant.html', {
        'form': form, 'recipient_key': recipient_key, 'recipient_public_key': b64(recipient_key.public_key),
        # The group keys to seal, as JSON in an attribute, which the template escapes.
        'group_keys': json.dumps([{'id': key.pk, 'public_key': b64(key.public_key)} for key in group_keys]),
        **unlock_context(request, own),
    }, status=400 if form.errors else 200)


# Rotations of the group key (keys.rotation, design §5.5): what the rotator's and checkers'
# browsers fetch and send. Only for a lawyer who holds the group key (§3.3).

def rotation_view(view):
    """lawyer_required, and the vault's own gate: nothing here for a lawyer without a grant."""
    @lawyer_required
    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        if not grants.reads_vault(request.user.lawyer_profile):
            raise Http404
        try:
            return view(request, *args, **kwargs)
        except rotation.Refused:
            return HttpResponse(status=409)
    return wrapped


@rotation_view
@require_GET
def rotation_role(request):
    """The lawyer's role in the attempt running, if any, and what their browser needs for it."""
    profile = request.user.lawyer_profile
    attempt = rotation.running_attempt()
    if attempt is None:
        return JsonResponse({'role': None})
    old, new = attempt.rotation.group_key, rotation.new_key_of(attempt)
    if attempt.rotator_id == profile.pk and attempt.resealed_at is None:
        if new is None:
            return JsonResponse({
                'role': 'rotator', 'stage': 'new_key', 'old_public_key': b64(old.public_key),
                'seal_to': [
                    {'lawyer_key': key.pk, 'public_key': b64(key.public_key)} for key in rotation.lawyer_keys_to_seal_to(attempt)
                ],
            })
        return JsonResponse({
            'role': 'rotator', 'stage': 'reseal', 'old_public_key': b64(old.public_key), 'new_public_key': b64(new.public_key),
        })
    try:
        check = rotation.checks_of(profile)
    except rotation.Refused:
        return JsonResponse({'role': None})
    return JsonResponse({
        'role': 'confirmer' if check.slot == RotationCheck.Slot.CONFIRMING else 'checker',
        # The old key too: a data key the rotator said they could not open is tried with it.
        'old_public_key': b64(old.public_key), 'new_public_key': b64(new.public_key),
    })


@rotation_view
@require_POST
def rotation_new_key(request):
    form = NewGroupKeyForm(request.POST)
    if not form.is_valid():
        return HttpResponse(status=400)
    rotation.submit_new_key(request.user.lawyer_profile, form.cleaned_data['public_key'], form.cleaned_data['grants'])
    return HttpResponse(status=201)


@rotation_view
@require_GET
def rotation_reseal(request):
    after = request.GET.get('after', '0')
    if not (after.isascii() and after.isdigit() and len(after) <= 18):
        return HttpResponse(status=400)
    batch = rotation.reseal_batch(request.user.lawyer_profile, after=int(after))
    return JsonResponse({'items': [_item_json(item) for item in batch]})


@rotation_view
@require_POST
def rotation_resealed(request):
    form = ResealedForm(request.POST)
    if not form.is_valid():
        return HttpResponse(status=400)
    stored = rotation.submit_resealed(request.user.lawyer_profile, form.cleaned_data['resealed'])
    return JsonResponse({'stored': stored})


@rotation_view
@require_POST
def rotation_unopenable(request):
    form = ItemIdsForm(request.POST)
    if not form.is_valid():
        return HttpResponse(status=400)
    rotation.report_unopenable(request.user.lawyer_profile, form.cleaned_data['ids'])
    return HttpResponse(status=204)


@rotation_view
@require_GET
def rotation_to_check(request):
    _, resealed, unopenable = rotation.to_check(request.user.lawyer_profile)
    return JsonResponse({
        'items': [_item_json(item) for item in resealed],
        # To open with the old key: if one opens, the rotator's claim was false.
        'unopenable': [_item_json(item) for item in unopenable],
    })


@rotation_view
@require_POST
def rotation_checked(request):
    form = CheckResultForm(request.POST)
    if not form.is_valid():
        return HttpResponse(status=400)
    passed = form.cleaned_data['result'] == 'passed'
    rotation.report(request.user.lawyer_profile, passed, form.cleaned_data['failed_items'], form.cleaned_data['new_public_key'])
    return HttpResponse(status=204)


def _item_json(item):
    return {'id': item['id'], 'sealed_data_key': b64(item['sealed_data_key']), 'key_check': b64(item['key_check'])}
