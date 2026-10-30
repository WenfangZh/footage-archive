"""Personal keys (docs/m5-encryption-design.md §3.4, §4, §5.1): an applicant or lawyer stores a
key their browser made, and fetches it back to unlock it; the server keeps only what the
browser sent, and never a key passphrase or recovery key.
"""

import base64
import hashlib
import os
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from nacl.public import PrivateKey

from accounts.models import LawyerProfile
from keys.models import ENCRYPTED_PRIVATE_KEY_BYTES, MEMLIMIT, OPSLIMIT, PUBLIC_KEY_BYTES, SALT_BYTES, LawyerKey, safety_number
from tests.conftest import log_in_with_second_factor, make_key

pytestmark = pytest.mark.django_db

User = get_user_model()
Status = LawyerProfile.Status


def sent_key():
    """What a browser sends once it has made a key: random bytes stand in for the parts."""
    return {
        # A real key: the form refuses what is not one.
        'public_key': bytes(PrivateKey.generate().public_key), 'passphrase_salt': os.urandom(SALT_BYTES),
        'private_key_under_passphrase': os.urandom(ENCRYPTED_PRIVATE_KEY_BYTES),
        'private_key_under_recovery_key': os.urandom(ENCRYPTED_PRIVATE_KEY_BYTES),
    }


def as_form(parts):
    return {name: base64.b64encode(value).decode() for name, value in parts.items()}


def person(status=Status.APPLICANT, email='camille@example.org'):
    user = User.objects.create_user(email, 'a long test passphrase')
    profile = LawyerProfile.objects.create(user=user, bar='Paris')
    if status in (Status.LAWYER, Status.SUSPENDED, Status.REVOKED):
        profile.verified_via, profile.vouched_by = 'The bar directory', 'A defence organisation'
        profile.save()
        profile.change_status(Status.LAWYER)
    if status != Status.LAWYER and status != Status.APPLICANT:
        profile.change_status(status)
    return profile


@pytest.fixture
def applicant():
    return person()


@pytest.fixture
def applicant_client(applicant):
    return log_in_with_second_factor(Client(), applicant.user)


def test_an_applicant_stores_the_key_their_browser_made(applicant, applicant_client):
    parts = sent_key()
    response = applicant_client.post(reverse('accounts:key_setup'), as_form(parts))
    assert response.status_code == 302 and response['Location'] == reverse('accounts:key')
    key = LawyerKey.objects.get(lawyer_profile=applicant)
    assert {name: bytes(getattr(key, name)) for name in parts} == parts
    # The Argon2id limits are the archive's, not whatever a browser might send.
    assert (key.opslimit, key.memlimit) == (OPSLIMIT, MEMLIMIT)
    assert key.admin_confirmed_at is None and key.retired_at is None


@pytest.mark.parametrize('name, value', [
    ('public_key', base64.b64encode(os.urandom(PUBLIC_KEY_BYTES - 1)).decode()),
    ('passphrase_salt', base64.b64encode(os.urandom(SALT_BYTES + 1)).decode()),
    ('private_key_under_passphrase', 'not base64!'),
    ('private_key_under_recovery_key', ''),
    # A point of small order: the server could seal no case label to it.
    ('public_key', base64.b64encode(bytes(PUBLIC_KEY_BYTES)).decode()),
])
def test_a_key_that_cannot_be_one_is_refused(applicant, applicant_client, name, value):
    response = applicant_client.post(reverse('accounts:key_setup'), {**as_form(sent_key()), name: value})
    assert response.status_code == 200
    assert not LawyerKey.objects.exists()


def test_a_key_someone_in_the_archive_has_had_is_refused(applicant, applicant_client):
    """Its safety number would name two people's keys, or an old one."""
    other = make_key(person(email='someone.else@example.org'), PrivateKey.generate())
    other.retire()
    sent = as_form(sent_key()) | {'public_key': base64.b64encode(bytes(other.public_key)).decode()}
    assert applicant_client.post(reverse('accounts:key_setup'), sent).status_code == 200
    assert not LawyerKey.objects.filter(lawyer_profile=applicant).exists()


def test_a_new_key_replaces_one_the_admin_has_not_confirmed_and_erases_it(applicant, applicant_client):
    old = make_key(applicant)
    applicant_client.post(reverse('accounts:key_setup'), as_form(sent_key()))
    old.refresh_from_db()
    assert old.retired_at is not None
    assert bytes(old.private_key_under_passphrase) == b'' and bytes(old.private_key_under_recovery_key) == b''
    assert LawyerKey.objects.current().filter(lawyer_profile=applicant).count() == 1


def test_a_key_the_admin_confirmed_is_not_replaced_by_setting_up_another(applicant, applicant_client):
    confirmed = make_key(applicant)
    confirmed.admin_confirmed_at = timezone.now()
    confirmed.save()
    response = applicant_client.post(reverse('accounts:key_setup'), as_form(sent_key()))
    assert response.status_code == 302
    assert list(LawyerKey.objects.current().filter(lawyer_profile=applicant)) == [confirmed]


def test_unlocking_fetches_ones_own_key(applicant, applicant_client):
    key = make_key(applicant)
    make_key(person(email='someone.else@example.org'))
    data = applicant_client.get(reverse('accounts:encrypted_key')).json()
    assert base64.b64decode(data['public_key']) == bytes(key.public_key)
    assert base64.b64decode(data['private_key_under_passphrase']) == bytes(key.private_key_under_passphrase)
    assert (data['opslimit'], data['memlimit']) == (key.opslimit, key.memlimit)
    # Only what unlocking with the passphrase needs.
    assert 'private_key_under_recovery_key' not in data


def test_only_a_reported_unlock_records_the_day(applicant, applicant_client):
    # Rotations draw among lawyers who unlocked recently (design §5.5): fetching the key is
    # not unlocking it, since the passphrase may be wrong.
    key = make_key(applicant)
    applicant_client.get(reverse('accounts:encrypted_key'))
    key.refresh_from_db()
    assert key.last_unlocked_on is None
    assert applicant_client.post(reverse('accounts:key_unlocked')).status_code == 204
    key.refresh_from_db()
    assert key.last_unlocked_on == timezone.localdate()


def test_unlocking_without_a_key_finds_nothing(applicant_client):
    assert applicant_client.get(reverse('accounts:encrypted_key')).status_code == 404


@pytest.mark.parametrize('kind', ['logged out', 'admin', 'declined', 'suspended', 'revoked', 'neither'])
def test_nobody_but_an_applicant_or_lawyer_reaches_the_key_pages(kind):
    client = Client()
    if kind == 'admin':
        log_in_with_second_factor(client, User.objects.create_superuser('admin@example.org'))
    elif kind == 'neither':
        log_in_with_second_factor(client, User.objects.create_user('neither@example.org', 'a long test passphrase'))
    elif kind != 'logged out':
        profile = person(Status(kind))
        make_key(profile)
        log_in_with_second_factor(client, profile.user)
    expected = 302 if kind == 'logged out' else 403  # To the login page, or refused.
    for name in ['accounts:key', 'accounts:key_setup', 'accounts:key_recover', 'accounts:key_reset', 'accounts:key_exposure', 'accounts:own_sealed', 'accounts:encrypted_key', 'accounts:own_grants']:
        assert client.get(reverse(name)).status_code == expected, name
    for name in ['accounts:key_unlocked', 'accounts:grant_checked', 'accounts:start_group_key']:
        assert client.post(reverse(name)).status_code == expected, name
    assert client.post(reverse('accounts:key_setup'), as_form(sent_key())).status_code == expected
    assert LawyerKey.objects.filter(last_unlocked_on__isnull=False).count() == 0
    assert LawyerKey.objects.count() == (0 if kind in ('logged out', 'admin', 'neither') else 1)


def test_a_lawyer_without_a_key_sets_one_up_which_starts_unconfirmed():
    """After a key reset (design §5.6), or admitted before keys existed: the admin confirms the
    new key before a lawyer can grant the group key to it."""
    lawyer = person(Status.LAWYER)
    client = log_in_with_second_factor(Client(), lawyer.user)
    assert client.get(reverse('accounts:key')).url == reverse('accounts:key_setup')
    assert client.post(reverse('accounts:key_setup'), as_form(sent_key())).status_code == 302
    key = LawyerKey.objects.get(lawyer_profile=lawyer)
    assert key.admin_confirmed_at is None and key.retired_at is None


def test_a_lawyer_cannot_replace_a_confirmed_key_outside_a_key_reset():
    lawyer = person(Status.LAWYER)
    key = make_key(lawyer)
    key.admin_confirmed_at = timezone.now()
    key.save()
    client = log_in_with_second_factor(Client(), lawyer.user)
    assert client.get(reverse('accounts:key_setup')).url == reverse('accounts:key')
    assert client.post(reverse('accounts:key_setup'), as_form(sent_key())).url == reverse('accounts:key')
    assert list(LawyerKey.objects.current()) == [key]


def test_the_setup_page_sends_only_the_public_key_and_the_encrypted_copies(applicant_client):
    # Every field with a name is sent; the passphrase and recovery key are typed in fields with
    # none, so they never reach the server.
    page = applicant_client.get(reverse('accounts:key_setup')).content.decode()
    form = page[page.index('<form id="key-setup"'):page.index('</form>', page.index('<form id="key-setup"'))]
    assert set(re.findall(r'name="([^"]+)"', form)) == {
        'csrfmiddlewaretoken', 'public_key', 'passphrase_salt', 'private_key_under_passphrase', 'private_key_under_recovery_key',
    }


def test_the_unlock_form_sends_nothing(applicant, applicant_client):
    make_key(applicant)
    page = applicant_client.get(reverse('accounts:key')).content.decode()
    form = page[page.index('<form id="unlock"'):page.index('</form>', page.index('<form id="unlock"'))]
    assert 'name=' not in form and 'action=' not in form


def test_a_lawyer_unlocks_their_key_too():
    lawyer = person(Status.LAWYER)
    make_key(lawyer)
    client = log_in_with_second_factor(Client(), lawyer.user)
    assert client.get(reverse('accounts:key')).status_code == 200
    assert client.get(reverse('accounts:encrypted_key')).status_code == 200
    assert client.post(reverse('accounts:key_unlocked')).status_code == 204


def test_the_key_page_ties_unlocked_keys_to_this_login(applicant, applicant_client):
    make_key(applicant)
    first = applicant_client.get(reverse('accounts:key')).context['login_id']
    assert applicant_client.get(reverse('accounts:key')).context['login_id'] == first
    again = log_in_with_second_factor(Client(), applicant.user)
    assert again.get(reverse('accounts:key')).context['login_id'] != first


def test_logging_out_tells_the_browser_to_forget_unlocked_keys(applicant_client):
    response = applicant_client.post(reverse('accounts:logout'))
    assert response['Clear-Site-Data'] == '"storage"'


def test_the_safety_number_is_six_groups_of_five_digits_from_blake2b():
    # Computed with hashlib, apart from PyNaCl, as the design states it (§3.4).
    public_key = bytes(range(32))
    digest = hashlib.blake2b(b'fa-safetynum-v1' + public_key, digest_size=32).digest()
    groups = [int.from_bytes(digest[i:i + 5], 'big') % 100_000 for i in range(0, 30, 5)]
    assert safety_number(public_key) == ' '.join(f'{group:05d}' for group in groups)
    assert len(safety_number(public_key).split()) == 6
