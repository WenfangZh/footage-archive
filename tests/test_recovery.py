"""Recovery (docs/m5-encryption-design.md §5.6): a new key passphrase set with the recovery key,
a key reset when both are lost, and the admin's confirmation of the new key.

The server cannot tell encrypted copies from random bytes, so random bytes stand in for what
the browser sends; the browser's part is checked by hand in a browser.
"""

import base64
import os
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import LawyerProfile
from audit.models import Action, AuditEntry
from keys import grants
from keys.models import ENCRYPTED_PRIVATE_KEY_BYTES, SALT_BYTES, GroupKey, KeyGrant, LawyerKey
from cases import labels
from tests.conftest import log_in_with_second_factor, make_case, make_key

pytestmark = pytest.mark.django_db

User = get_user_model()
Status = LawyerProfile.Status


def b64(value):
    return base64.b64encode(bytes(value)).decode()


def lawyer(email='lawyer@example.org', confirmed=True):
    profile = LawyerProfile.objects.create(user=User.objects.create_user(email, 'a long test passphrase'), bar='Paris')
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
    assert client.post(reverse('accounts:key_reset'), {'key': key.pk}).status_code == 400
    response = client.post(reverse('accounts:key_reset'), {'key': key.pk, 'lost_both': 'on'})
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


def test_a_reset_page_shown_before_another_reset_resets_nothing():
    """Open in two tabs: reset in one, a new key set up; the other's form must not reset it."""
    profile = lawyer()
    client = client_for(profile)
    old = grants.current_key(profile)
    client.post(reverse('accounts:key_reset'), {'key': old.pk, 'lost_both': 'on'})
    new = make_key(profile)
    response = client.post(reverse('accounts:key_reset'), {'key': old.pk, 'lost_both': 'on'})
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
    client.post(reverse('accounts:key_reset'), {'key': grants.current_key(profile).pk, 'lost_both': 'on'})
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
