"""Recovery (docs/m5-encryption-design.md §5.6): a new key passphrase set with the recovery key,
a key reset when both are lost, and the admin's confirmation of the new key.

The server cannot tell encrypted copies from random bytes, so random bytes stand in for what
the browser sends; the browser's part is checked by hand in a browser.
"""

import base64
import json
import os
import re

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from nacl.public import PrivateKey, SealedBox

from accounts.models import LawyerProfile
from audit.models import Action, AuditEntry
from cases import labels
from cases.models import Case
from clips import rate_limits, vault
from clips.models import Clip, Message
from keys import grants
from keys.models import ENCRYPTED_PRIVATE_KEY_BYTES, SALT_BYTES, GroupKey, KeyGrant, LawyerKey
from tests.conftest import TEST_CODE, log_in_with_second_factor, make_case, make_key, open_label, with_code_holder_key

pytestmark = pytest.mark.django_db

User = get_user_model()
Status = LawyerProfile.Status


def cache_count(request, action, account=None):
    return cache.get(rate_limits.cache_key(request, action, account), 0)


def b64(value):
    return base64.b64encode(bytes(value)).decode()


PASSWORD = 'a long test passphrase'


def lawyer(email='lawyer@example.org', confirmed=True):
    profile = LawyerProfile.objects.create(user=User.objects.create_user(email, PASSWORD), bar='Paris')
    profile.verified_via, profile.vouched_by = 'The bar directory', 'A defence organisation'
    profile.save()
    profile.change_status(Status.LAWYER)
    key = make_key(profile)
    if confirmed:
        key.admin_confirmed_at = timezone.now()
        key.save()
    return profile


def give(group_key, *profiles):
    for profile in profiles:
        KeyGrant.objects.create(
            group_key=group_key, lawyer_key=grants.current_key(profile), sealed_private_key=os.urandom(80), granted_by=profile,
        )


def client_for(profile):
    return log_in_with_second_factor(Client(), profile.user)


@pytest.fixture
def admin_client():
    return log_in_with_second_factor(Client(), User.objects.create_superuser('admin@example.org'))


# A new key passphrase, set with the recovery key.

def new_copy(key):
    return {
        'public_key': b64(key.public_key), 'passphrase_salt': b64(os.urandom(SALT_BYTES)),
        'private_key_under_passphrase': b64(os.urandom(ENCRYPTED_PRIVATE_KEY_BYTES)),
    }


def test_only_the_recovery_page_carries_the_copy_the_recovery_key_opens():
    profile = lawyer()
    key = grants.current_key(profile)
    client = client_for(profile)
    copy = b64(key.private_key_under_recovery_key)
    assert f'data-recovery-copy="{copy}"' in client.get(reverse('accounts:key_recover')).content.decode()
    assert copy not in client.get(reverse('accounts:key')).content.decode()


def test_a_new_passphrase_replaces_only_the_copy_it_opens():
    profile = lawyer()
    group_key = GroupKey.objects.create(public_key=os.urandom(32))
    give(group_key, profile)
    key = grants.current_key(profile)
    before = bytes(key.private_key_under_recovery_key)
    sent = new_copy(key)
    response = client_for(profile).post(reverse('accounts:key_recover'), sent)
    assert response.url == reverse('accounts:key')
    key.refresh_from_db()
    # The same key, safety number, confirmation and grant; the recovery key stays valid.
    assert key.retired_at is None and key.admin_confirmed_at is not None and bytes(key.private_key_under_recovery_key) == before
    assert b64(key.passphrase_salt) == sent['passphrase_salt']
    assert b64(key.private_key_under_passphrase) == sent['private_key_under_passphrase']
    assert grants.reads_vault(profile)
    # The browser opened and checked the key, as an unlock does: it counts for rotation draws.
    assert key.last_unlocked_on == timezone.localdate()
    entry = AuditEntry.objects.get(action=Action.PASSPHRASE_REPLACEMENT)
    assert entry.lawyer_profile == profile and entry.lawyer_key == key


def test_a_new_passphrase_for_another_key_is_refused():
    """A key replaced in another tab meanwhile: its successor never gets the old key's copy."""
    profile = lawyer()
    key = grants.current_key(profile)
    before = bytes(key.private_key_under_passphrase)
    sent = new_copy(key) | {'public_key': b64(os.urandom(32))}
    response = client_for(profile).post(reverse('accounts:key_recover'), sent)
    assert response.url == reverse('accounts:key_recover')
    key.refresh_from_db()
    assert bytes(key.private_key_under_passphrase) == before
    assert not AuditEntry.objects.filter(action=Action.PASSPHRASE_REPLACEMENT).exists()


def test_the_recovery_page_sends_only_the_new_copy():
    # The recovery key and the new passphrase are typed in fields with no name.
    page = client_for(lawyer()).get(reverse('accounts:key_recover')).content.decode()
    forms = page[page.index('<form id="recover"'):page.index('</form>', page.index('<form id="new-passphrase"'))]
    assert set(re.findall(r'name="([^"]+)"', forms)) == {
        'csrfmiddlewaretoken', 'public_key', 'passphrase_salt', 'private_key_under_passphrase',
    }


# A key reset, when both are lost.

def test_a_key_reset_retires_the_key_and_its_grants():
    profile, colleague = lawyer(), lawyer('colleague@example.org')
    group_key = GroupKey.objects.create(public_key=os.urandom(32))
    give(group_key, profile, colleague)
    key = grants.current_key(profile)
    case = make_case(profile, 'Filmed near the station')
    client = client_for(profile)
    assert client.post(reverse('accounts:key_reset'), {'key': key.pk, 'password': PASSWORD}).status_code == 400
    response = client.post(reverse('accounts:key_reset'), {'key': key.pk, 'lost_both': 'on', 'password': PASSWORD})
    assert response.url == reverse('accounts:key_setup')
    key.refresh_from_db()
    # What was sealed only to it is lost (design §5.6).
    case.refresh_from_db()
    assert labels.unreadable(case) and labels.for_page(case) == ''
    assert key.retired_at is not None and bytes(key.private_key_under_passphrase) == b''
    assert not KeyGrant.objects.filter(lawyer_key=key).exists()
    assert not grants.reads_vault(profile) and grants.reads_vault(colleague)
    entry = AuditEntry.objects.get(action=Action.KEY_RESET)
    assert entry.lawyer_profile == profile and entry.lawyer_key == key and entry.admin is None


def test_a_reset_needs_the_login_password_and_counts_wrong_ones_as_failed_logins(settings):
    """A session left open is not enough, nor a way to guess the password (design §5.6)."""
    settings.RATE_LIMIT_FAILED_LOGINS_FOR_ACCOUNT = 3
    profile = lawyer()
    key = grants.current_key(profile)
    client = client_for(profile)
    wrong = {'key': key.pk, 'lost_both': 'on', 'password': 'not the password'}
    for _ in range(3):
        assert client.post(reverse('accounts:key_reset'), wrong).status_code == 400
    # Over the login's limit: refused before the password is checked, even the right one.
    right = wrong | {'password': PASSWORD}
    assert client.post(reverse('accounts:key_reset'), right).status_code == 429
    key.refresh_from_db()
    assert key.retired_at is None


def test_a_right_password_takes_back_its_count():
    """Each attempt is counted in advance, as at login: one that succeeds is taken back."""
    profile = lawyer()
    client = client_for(profile)
    page = client.post(reverse('accounts:key_reset'), {'key': grants.current_key(profile).pk, 'password': PASSWORD})
    # The checkbox is missing, but the password was right: not a failed login.
    assert page.status_code == 400 and 'password' not in page.context['form'].errors
    assert cache_count(page.wsgi_request, rate_limits.FAILED_LOGINS) == 0


def test_a_reset_page_shown_before_another_reset_resets_nothing():
    """Open in two tabs: reset in one, a new key set up; the other's form must not reset it."""
    profile = lawyer()
    client = client_for(profile)
    old = grants.current_key(profile)
    client.post(reverse('accounts:key_reset'), {'key': old.pk, 'lost_both': 'on', 'password': PASSWORD})
    new = make_key(profile)
    response = client.post(reverse('accounts:key_reset'), {'key': old.pk, 'lost_both': 'on', 'password': PASSWORD})
    assert response.url == reverse('accounts:key')
    new.refresh_from_db()
    assert new.retired_at is None and AuditEntry.objects.filter(action=Action.KEY_RESET).count() == 1


def test_setting_up_never_replaces_a_lawyers_key_without_a_reset():
    """Not even one the admin has not confirmed yet: case labels may be sealed to it, and only
    a key reset, which the audit log records, may lose them."""
    profile = lawyer(confirmed=False)
    key = grants.current_key(profile)
    client = client_for(profile)
    assert client.get(reverse('accounts:key_setup')).url == reverse('accounts:key')
    sent = {name: b64(os.urandom(size)) for name, size in [
        ('public_key', 32), ('passphrase_salt', SALT_BYTES), ('private_key_under_passphrase', ENCRYPTED_PRIVATE_KEY_BYTES),
        ('private_key_under_recovery_key', ENCRYPTED_PRIVATE_KEY_BYTES),
    ]}
    assert client.post(reverse('accounts:key_setup'), sent).url == reverse('accounts:key')
    assert list(LawyerKey.objects.current().filter(lawyer_profile=profile)) == [key]


def test_the_reset_page_warns_the_last_holder(settings):
    settings.LANGUAGE_CODE = 'en'
    profile = lawyer()
    give(GroupKey.objects.create(public_key=os.urandom(32)), profile)
    page = client_for(profile).get(reverse('accounts:key_reset'), HTTP_ACCEPT_LANGUAGE='en').content.decode()
    assert 'You are the last lawyer holding the group key' in page


def test_after_a_reset_the_new_key_waits_for_the_admin_then_a_grant(admin_client):
    """The admin's comparison and a lawyer's grant, as for a newcomer (design §5.2, §5.3)."""
    profile, colleague = lawyer(), lawyer('colleague@example.org')
    group_key = GroupKey.objects.create(public_key=os.urandom(32))
    give(group_key, profile, colleague)
    client = client_for(profile)
    client.post(reverse('accounts:key_reset'), {'key': grants.current_key(profile).pk, 'lost_both': 'on', 'password': PASSWORD})
    new = make_key(profile)
    assert grants.waiting() == []
    assert reverse('manage:confirm_key', args=[profile.pk]) in admin_client.get(reverse('manage:home')).content.decode()
    page = admin_client.get(reverse('manage:confirm_key', args=[profile.pk]))
    assert new.safety_number() in page.content.decode()
    assert admin_client.post(reverse('manage:confirm_key', args=[profile.pk]), {'key': new.pk}).status_code == 400
    response = admin_client.post(reverse('manage:confirm_key', args=[profile.pk]), {'key': new.pk, 'key_confirmed': 'on'})
    assert response.url == reverse('manage:home')
    new.refresh_from_db()
    assert new.admin_confirmed_at is not None
    entry = AuditEntry.objects.get(action=Action.KEY_CONFIRMATION)
    assert entry.lawyer_profile == profile and entry.lawyer_key == new and entry.admin is not None
    assert grants.waiting() == [new]
    # Still nothing from the vault until a lawyer grants the group key.
    assert not grants.reads_vault(profile)
    assert admin_client.get(reverse('manage:confirm_key', args=[profile.pk])).status_code == 404


def test_the_admin_confirms_only_the_key_they_compared(admin_client):
    profile = lawyer(confirmed=False)
    compared = grants.current_key(profile)
    compared.retire()
    newer = make_key(profile)
    response = admin_client.post(reverse('manage:confirm_key', args=[profile.pk]), {'key': compared.pk, 'key_confirmed': 'on'})
    assert response.url == reverse('manage:confirm_key', args=[profile.pk])
    newer.refresh_from_db()
    assert newer.admin_confirmed_at is None and not AuditEntry.objects.filter(action=Action.KEY_CONFIRMATION).exists()


def test_the_confirmation_page_is_for_a_lawyers_unconfirmed_key_only(admin_client):
    confirmed = lawyer()
    applicant = LawyerProfile.objects.create(user=User.objects.create_user('applicant@example.org'), bar='Paris')
    make_key(applicant)
    for profile in (confirmed, applicant):
        assert admin_client.get(reverse('manage:confirm_key', args=[profile.pk])).status_code == 404
    unconfirmed = lawyer('new@example.org', confirmed=False)
    # Only the admin: a lawyer cannot confirm their own key, nor a colleague's.
    assert client_for(confirmed).get(reverse('manage:confirm_key', args=[unconfirmed.pk])).status_code == 403


# A key passphrase or recovery key that may have been seen.

def lawyer_with_key(email):
    """A lawyer holding the group key, whose personal key's private half the test knows."""
    profile = lawyer(email, confirmed=False)
    grants.current_key(profile).retire()
    private_key = PrivateKey.generate()
    key = make_key(profile, private_key)
    key.admin_confirmed_at = timezone.now()
    key.save()
    return profile, private_key


@pytest.fixture
def exposed():
    """A lawyer with two labelled cases and a thread of two messages, and a colleague who could
    grant the group key."""
    group_key = GroupKey.objects.create(public_key=bytes(PrivateKey.generate().public_key))
    profile, private_key = lawyer_with_key('lawyer@example.org')
    colleague, _ = lawyer_with_key('colleague@example.org')
    give(group_key, profile, colleague)
    cases = [make_case(profile, 'Filmed near the station'), make_case(profile, 'Second case')]
    clip = with_code_holder_key(Clip.objects.create(code_hash='0' * 64))
    messages = [
        vault.send_message(clip, profile, Message.Direction.TO_CODE_HOLDER, 'Were you near the station?'),
        vault.send_message(clip, profile, Message.Direction.FROM_CODE_HOLDER, 'Yes, from the corner.'),
    ]
    vault.send_message(clip, colleague, Message.Direction.TO_CODE_HOLDER, 'Not theirs')
    return {
        'profile': profile, 'private_key': private_key, 'cases': cases, 'group_key': group_key,
        'clip': clip, 'messages': messages,
    }


def resealed_to(new_private_key, private_key, sealed_key):
    return SealedBox(new_private_key.public_key).encrypt(SealedBox(private_key).decrypt(bytes(sealed_key)))


def report(
    profile, private_key, new_private_key, resealed=None, unreadable=(), messages_resealed=None, messages_unreadable=(),
    **changes,
):
    """What the browser sends (keys/static/keys/exposure.js): each label's and message's data key,
    opened with the old key and sealed to the new one, unless `resealed` or `messages_resealed`
    says otherwise."""
    old = grants.current_key(profile)
    if resealed is None:
        resealed = {
            case.pk: resealed_to(new_private_key, private_key, case.label_sealed_key)
            for case in Case.objects.filter(label_key=old) if case.pk not in unreadable
        }
    if messages_resealed is None:
        messages_resealed = {
            message.pk: resealed_to(new_private_key, private_key, message.lawyer_sealed_key)
            for message in Message.objects.filter(lawyer_key=old) if message.pk not in messages_unreadable
        }
    fields = {
        'public_key': b64(new_private_key.public_key), 'passphrase_salt': b64(os.urandom(SALT_BYTES)),
        'private_key_under_passphrase': b64(os.urandom(ENCRYPTED_PRIVATE_KEY_BYTES)),
        'private_key_under_recovery_key': b64(os.urandom(ENCRYPTED_PRIVATE_KEY_BYTES)),
        'old_public_key': b64(old.public_key), 'password': PASSWORD,
        'resealed_labels': json.dumps({str(pk): b64(sealed) for pk, sealed in resealed.items()}),
        'unreadable_labels': json.dumps(list(unreadable)),
        'resealed_messages': json.dumps({str(pk): b64(sealed) for pk, sealed in messages_resealed.items()}),
        'unreadable_messages': json.dumps([str(pk) for pk in messages_unreadable]),
    } | changes
    return client_for(profile).post(reverse('accounts:key_exposure'), fields)


def test_an_exposure_report_replaces_the_key_keeps_the_labels_and_requests_a_rotation(exposed):
    profile, cases = exposed['profile'], exposed['cases']
    old = grants.current_key(profile)
    new_private_key = PrivateKey.generate()
    response = report(profile, exposed['private_key'], new_private_key)
    assert response.status_code == 201 and response['Clear-Site-Data'] == '"storage"'
    old.refresh_from_db()
    new = grants.current_key(profile)
    assert old.retired_at is not None and bytes(new.public_key) == bytes(new_private_key.public_key)
    # Waiting for the admin's comparison and a lawyer's grant, like a newcomer's (§5.2, §5.3).
    assert new.admin_confirmed_at is None and not KeyGrant.objects.filter(lawyer_key__in=[old, new]).exists()
    assert not grants.reads_vault(profile)
    for case, label in zip(cases, ['Filmed near the station', 'Second case']):
        case.refresh_from_db()
        assert open_label(case, new_private_key) == label
    exposed['group_key'].refresh_from_db()
    assert exposed['group_key'].status == GroupKey.Status.ROTATION_REQUESTED
    entry = AuditEntry.objects.get(action=Action.KEY_EXPOSURE_REPORT)
    assert entry.lawyer_profile == profile and entry.lawyer_key == old
    assert AuditEntry.objects.filter(action=Action.ROTATION_REQUEST, lawyer_profile=profile).exists()
    # Not again before the new key holds the group key: reports cannot keep abandoning rotations.
    assert report(profile, new_private_key, PrivateKey.generate()).status_code == 403


def opened_by_lawyer(message, private_key):
    """A message's text as its lawyer's browser opens it."""
    from nacl import bindings
    sealed = vault.sealed_message(message)
    data_key = SealedBox(private_key).decrypt(sealed['sealed_key'])
    encrypted = sealed['ciphertext']
    padded = bindings.crypto_aead_xchacha20poly1305_ietf_decrypt(
        encrypted[vault.NONCE_BYTES:], sealed['additional_data'], encrypted[:vault.NONCE_BYTES], data_key,
    )
    return bindings.sodium_unpad(padded, vault.TEXT_BLOCK).decode()


def test_an_exposure_report_moves_the_lawyers_messages_to_the_new_key(exposed):
    profile, clip = exposed['profile'], exposed['clip']
    new_private_key = PrivateKey.generate()
    assert report(profile, exposed['private_key'], new_private_key).status_code == 201
    new = grants.current_key(profile)
    for message, text in zip(exposed['messages'], ['Were you near the station?', 'Yes, from the corner.']):
        message.refresh_from_db()
        assert message.lawyer_key == new and opened_by_lawyer(message, new_private_key) == text
    # The code holder's copies, and the colleague's thread, are untouched.
    assert [text for _, text in vault.code_holder_messages(clip, TEST_CODE)] == [
        'Were you near the station?', 'Yes, from the corner.', 'Not theirs',
    ]
    assert Message.objects.filter(lawyer_profile__user__email='colleague@example.org').get().lawyer_key != new


def test_a_report_missing_a_message_sent_meanwhile_is_refused_and_changes_nothing(exposed):
    profile = exposed['profile']
    old = grants.current_key(profile)
    new_private_key = PrivateKey.generate()
    first, missing = exposed['messages']
    resealed = {first.pk: resealed_to(new_private_key, exposed['private_key'], first.lawyer_sealed_key)}
    assert report(profile, exposed['private_key'], new_private_key, messages_resealed=resealed).status_code == 409
    old.refresh_from_db()
    missing.refresh_from_db()
    assert old.retired_at is None and missing.lawyer_key == old


def test_a_message_the_browser_could_not_open_stays_with_the_old_key(exposed):
    profile = exposed['profile']
    unopened = exposed['messages'][1]
    response = report(profile, exposed['private_key'], PrivateKey.generate(), messages_unreadable=(unopened.pk,))
    assert response.status_code == 201
    shown = [str(message) for message in get_messages(response.wsgi_request)]
    # In the request's language: '1 message could not…' or '1 message n'a pas…'.
    assert any(message.startswith('1 message ') for message in shown)
    unopened.refresh_from_db()
    assert vault.sealed_message(unopened) is None
    assert vault.code_holder_messages(exposed['clip'], TEST_CODE)[1][1] == 'Yes, from the corner.'


# A sealed key of the right length, so that only the id can be refused.
SEALED = b64(os.urandom(80))


@pytest.mark.parametrize('value', [
    f'{{"1": "{SEALED}"}}', f'{{"7c9e6679-7425-40de-944b-E07FC1F90AE7": "{SEALED}"}}',
    f'{{"7c9e6679742540de944be07fc1f90ae7": "{SEALED}"}}', '[]',
])
def test_resealed_messages_are_keyed_by_message_id(exposed, value):
    response = report(exposed['profile'], exposed['private_key'], PrivateKey.generate(), resealed_messages=value)
    assert response.status_code == 400
    assert grants.current_key(exposed['profile']).retired_at is None


def test_a_report_missing_a_label_made_meanwhile_is_refused_and_changes_nothing(exposed):
    profile = exposed['profile']
    old = grants.current_key(profile)
    new_private_key = PrivateKey.generate()
    missing = exposed['cases'][1]
    resealed = {exposed['cases'][0].pk: SealedBox(new_private_key.public_key).encrypt(os.urandom(32))}
    assert report(profile, exposed['private_key'], new_private_key, resealed=resealed).status_code == 409
    old.refresh_from_db()
    missing.refresh_from_db()
    assert old.retired_at is None and missing.label_key == old and grants.reads_vault(profile)
    assert not AuditEntry.objects.filter(action=Action.KEY_EXPOSURE_REPORT).exists()


def test_a_label_the_browser_could_not_open_stays_with_the_old_key(exposed):
    profile = exposed['profile']
    unopened = exposed['cases'][1]
    assert report(profile, exposed['private_key'], PrivateKey.generate(), unreadable=(unopened.pk,)).status_code == 201
    unopened.refresh_from_db()
    assert labels.unreadable(unopened)


def test_the_last_lawyer_who_could_grant_cannot_report(exposed):
    """A suspended colleague's grant does not count: they could not give the group key."""
    colleague = LawyerProfile.objects.get(user__email='colleague@example.org')
    colleague.change_status(Status.SUSPENDED)
    profile = exposed['profile']
    page = client_for(profile).get(reverse('accounts:key_exposure'))
    assert not page.context['another_can_grant'] and 'id="exposure"' not in page.content.decode()
    assert report(profile, exposed['private_key'], PrivateKey.generate()).status_code == 403
    assert grants.current_key(profile).retired_at is None and grants.reads_vault(profile)


@pytest.mark.parametrize('change', ['wrong password', 'the same key'])
def test_a_report_without_the_password_or_a_new_key_is_refused(exposed, change):
    profile, private_key = exposed['profile'], exposed['private_key']
    if change == 'wrong password':
        response = report(profile, private_key, PrivateKey.generate(), password='not the password')
        assert response.status_code == 400 and response.json() == {'wrong_password': True}
    else:
        # Refused where it enters, as is any key the archive has had.
        assert report(profile, private_key, private_key).status_code == 400
    assert grants.current_key(profile).retired_at is None


def test_the_labels_a_browser_reseals_are_its_own_on_its_current_key(exposed):
    profile = exposed['profile']
    colleague = LawyerProfile.objects.get(user__email='colleague@example.org')
    make_case(colleague, 'Not theirs')
    sent = client_for(profile).get(reverse('accounts:own_sealed')).json()
    assert sent['public_key'] == b64(grants.current_key(profile).public_key)
    assert [label['id'] for label in sent['labels']] == [case.pk for case in exposed['cases']]
    assert all(label['key_check'] == b64(Case.objects.get(pk=label['id']).label_key_check) for label in sent['labels'])
    assert sorted(message['id'] for message in sent['messages']) == sorted(str(message.pk) for message in exposed['messages'])
    assert all(
        message['key_check'] == b64(Message.objects.get(pk=message['id']).key_check) for message in sent['messages']
    )
