"""The group key and key grants (docs/m5-encryption-design.md §3.3, §5.3, §5.7, §10).

The first lawyer's browser makes the group key; a lawyer holding it grants it to an admitted
colleague; the vault serves only lawyers whose key holds a grant, and uploads are open only
while someone who is not revoked holds one. The server cannot tell sealed keys from random
bytes, so random bytes stand in for them.
"""

import base64
import datetime
import json
import os

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import LawyerProfile
from alerts.models import Alert
from audit.models import Action, AuditEntry
from clips import grid, vault
from clips.models import Clip, Original, Precision
from keys import grants
from keys.models import PUBLIC_KEY_BYTES, SEALED_GROUP_KEY_BYTES, GroupKey, KeyGrant
from tests.conftest import BASTILLE, log_in_with_second_factor, make_case, make_key

pytestmark = pytest.mark.django_db

User = get_user_model()
Status = LawyerProfile.Status
GroupStatus = GroupKey.Status


def b64(value):
    return base64.b64encode(value).decode()


def sealed():
    return os.urandom(SEALED_GROUP_KEY_BYTES)


def person(email, status=Status.LAWYER, confirmed=True):
    """An applicant or lawyer with a personal key, confirmed by the admin unless asked not to."""
    profile = LawyerProfile.objects.create(user=User.objects.create_user(email, first_name='Camille'), bar='Paris')
    profile.vouched_by = 'A defence organisation'
    if status != Status.APPLICANT:
        profile.verified_via = 'The bar directory'
        profile.save()
        profile.change_status(Status.LAWYER)
        if status != Status.LAWYER:
            profile.change_status(status)
    key = make_key(profile)
    if confirmed:
        key.admin_confirmed_at = timezone.now()
        key.save()
    return profile


def client_for(profile):
    return log_in_with_second_factor(Client(), profile.user)


def group_key(*holders, status=GroupStatus.CURRENT):
    key = GroupKey.objects.create(public_key=os.urandom(PUBLIC_KEY_BYTES), status=status)
    for holder in holders:
        KeyGrant.objects.create(
            group_key=key, lawyer_key=grants.current_key(holder), sealed_private_key=sealed(), granted_by=holder,
        )
    return key


def start(client):
    return client.post(reverse('accounts:start_group_key'), {
        'public_key': b64(os.urandom(PUBLIC_KEY_BYTES)), 'sealed_private_key': b64(sealed()),
    })


# The first lawyer (design §5.3) and starting over (§5.7).

def test_the_first_lawyer_makes_the_group_key_and_holds_it():
    first = person('first@example.org')
    client = client_for(first)
    assert client.get(reverse('accounts:own_grants')).json() == {'grants': [], 'may_start': True}
    assert start(client).status_code == 201
    key = GroupKey.objects.get()
    assert key.status == GroupStatus.CURRENT
    grant = KeyGrant.objects.get()
    assert (grant.group_key, grant.lawyer_key, grant.granted_by) == (key, grants.current_key(first), first)
    entry = AuditEntry.objects.get()
    assert (entry.action, entry.lawyer_profile) == (Action.GROUP_KEY_CREATION, first)
    held = client.get(reverse('accounts:own_grants')).json()
    assert held['may_start'] is False
    assert [item['group_public_key'] for item in held['grants']] == [b64(bytes(key.public_key))]
    assert grants.reads_vault(first)


def test_no_second_group_key_while_a_lawyer_who_is_not_revoked_holds_one():
    holder = person('holder@example.org', Status.SUSPENDED)
    group_key(holder)
    other = person('other@example.org')
    client = client_for(other)
    assert client.get(reverse('accounts:own_grants')).json()['may_start'] is False
    assert start(client).status_code == 409
    assert GroupKey.objects.count() == 1 and not AuditEntry.objects.exists()


def test_once_every_holder_is_revoked_the_next_lawyer_starts_over():
    holder = person('holder@example.org')
    old = group_key(holder, status=GroupStatus.RETIRING)
    new = group_key(holder, status=GroupStatus.CHECKING)
    holder.change_status(Status.REVOKED)
    other = person('other@example.org')
    assert start(client_for(other)).status_code == 201
    old.refresh_from_db()
    new.refresh_from_db()
    assert (old.status, new.status) == (GroupStatus.RETIRED, GroupStatus.ABANDONED)
    assert GroupKey.objects.get(status=GroupStatus.CURRENT).grants.get().lawyer_key.lawyer_profile == other
    assert AuditEntry.objects.get().action == Action.STARTING_OVER


def test_only_a_lawyer_with_a_confirmed_key_makes_the_group_key():
    applicant = person('applicant@example.org', Status.APPLICANT, confirmed=False)
    assert start(client_for(applicant)).status_code == 403
    # Admitted before keys existed: nothing to seal it to.
    keyless = LawyerProfile.objects.create(user=User.objects.create_user('keyless@example.org'), bar='Paris')
    keyless.verified_via, keyless.vouched_by = 'The bar directory', 'A defence organisation'
    keyless.save()
    keyless.change_status(Status.LAWYER)
    assert start(client_for(keyless)).status_code == 409
    assert not GroupKey.objects.exists()


def test_a_group_key_that_cannot_be_one_is_refused():
    client = client_for(person('first@example.org'))
    response = client.post(reverse('accounts:start_group_key'), {
        'public_key': b64(os.urandom(PUBLIC_KEY_BYTES)), 'sealed_private_key': b64(os.urandom(SEALED_GROUP_KEY_BYTES - 1)),
    })
    assert response.status_code == 400 and not GroupKey.objects.exists()


def test_the_database_allows_one_established_group_key():
    group_key()
    with pytest.raises(IntegrityError):
        group_key(status=GroupStatus.ROTATION_REQUESTED)


# Key grants (design §5.3).

@pytest.fixture
def holder():
    profile = person('holder@example.org')
    group_key(profile)
    return profile


@pytest.fixture
def newcomer():
    return person('newcomer@example.org')


def grant_url(profile):
    return reverse('lawyer:grant', kwargs={'lawyer_key_id': grants.current_key(profile).pk})


def grant_form(**sealed_keys):
    return {'grants': json.dumps(sealed_keys), 'safety_number_compared': 'on'}


def all_missing(profile):
    return {str(key.pk): b64(sealed()) for key in grants.missing(grants.current_key(profile))}


def test_a_holder_grants_the_group_key_to_an_admitted_colleague(holder, newcomer):
    client = client_for(holder)
    page = client.get(grant_url(newcomer))
    assert page.status_code == 200
    # The browser computes the safety number from exactly this public key, and seals to it.
    assert page.context['recipient_public_key'] == b64(bytes(grants.current_key(newcomer).public_key))
    sealed_keys = all_missing(newcomer)
    response = client.post(grant_url(newcomer), grant_form(**sealed_keys))
    assert response.status_code == 302 and response['Location'] == reverse('lawyer:waiting')
    grant = KeyGrant.objects.get(lawyer_key__lawyer_profile=newcomer)
    assert grant.granted_by == holder and grant.checked_at is None
    assert b64(bytes(grant.sealed_private_key)) == next(iter(sealed_keys.values()))
    entry = AuditEntry.objects.get()
    assert (entry.action, entry.lawyer_profile, entry.lawyer_key) == (Action.KEY_GRANT, holder, grant.lawyer_key)
    assert grants.reads_vault(newcomer)


def test_a_grant_needs_the_safety_number_compared(holder, newcomer):
    response = client_for(holder).post(grant_url(newcomer), {'grants': json.dumps(all_missing(newcomer))})
    assert response.status_code == 400 and not grants.reads_vault(newcomer)


@pytest.mark.parametrize('recipient', ['applicant', 'suspended', 'revoked', 'unconfirmed', 'holding', 'self'])
def test_a_grant_goes_only_to_an_admitted_unsuspended_colleague_with_a_confirmed_key(holder, recipient):
    if recipient == 'self':
        profile = holder
    elif recipient == 'holding':
        profile = person('holding@example.org')
        KeyGrant.objects.create(
            group_key=GroupKey.objects.get(), lawyer_key=grants.current_key(profile), sealed_private_key=sealed(),
            granted_by=holder,
        )
    else:
        status = {'applicant': Status.APPLICANT, 'suspended': Status.SUSPENDED, 'revoked': Status.REVOKED}.get(recipient, Status.LAWYER)
        profile = person('recipient@example.org', status, confirmed=recipient != 'unconfirmed')
    client = client_for(holder)
    count = KeyGrant.objects.count()
    assert client.get(grant_url(profile)).status_code == 404
    form = grant_form(**{str(GroupKey.objects.get().pk): b64(sealed())})
    assert client.post(grant_url(profile), form).status_code == 404
    assert KeyGrant.objects.count() == count and not AuditEntry.objects.exists()


def test_no_grant_from_a_lawyer_who_holds_none(newcomer):
    group_key(person('holder@example.org'))
    without = person('without@example.org')
    client = client_for(without)
    assert client.get(grant_url(newcomer)).status_code == 404
    assert client.post(grant_url(newcomer), grant_form(**all_missing(newcomer))).status_code == 404
    assert not grants.reads_vault(newcomer)


def test_during_a_rotation_a_grant_covers_both_group_keys(newcomer):
    holder = person('holder@example.org')
    old = group_key(holder, status=GroupStatus.RETIRING)
    new = group_key(holder, status=GroupStatus.CHECKING)
    client = client_for(holder)
    only_old = grant_form(**{str(old.pk): b64(sealed())})
    response = client.post(grant_url(newcomer), only_old)
    assert response.status_code == 302 and response['Location'] == grant_url(newcomer)
    assert not KeyGrant.objects.filter(lawyer_key__lawyer_profile=newcomer).exists()
    assert client.post(grant_url(newcomer), grant_form(**{str(old.pk): b64(sealed()), str(new.pk): b64(sealed())})).status_code == 302
    assert set(KeyGrant.objects.filter(lawyer_key__lawyer_profile=newcomer).values_list('group_key', flat=True)) == {old.pk, new.pk}


def test_a_lawyer_grants_only_group_keys_they_hold(newcomer):
    holder = person('holder@example.org')
    old = group_key(holder, status=GroupStatus.RETIRING)
    new = group_key(status=GroupStatus.CHECKING)
    form = grant_form(**{str(old.pk): b64(sealed()), str(new.pk): b64(sealed())})
    assert client_for(holder).post(grant_url(newcomer), form).status_code == 302
    assert not KeyGrant.objects.filter(lawyer_key__lawyer_profile=newcomer).exists()


@pytest.mark.parametrize('value', ['not json', '[]', '{"x": "AAAA"}', json.dumps({'1': b64(os.urandom(10))})])
def test_grants_that_cannot_be_read_are_refused(holder, newcomer, value):
    response = client_for(holder).post(grant_url(newcomer), {'grants': value, 'safety_number_compared': 'on'})
    assert response.status_code == 400 and not grants.reads_vault(newcomer)


def test_holders_see_who_is_waiting_for_the_group_key(holder, newcomer):
    person('applicant@example.org', Status.APPLICANT)
    person('suspended@example.org', Status.SUSPENDED)
    page = client_for(holder).get(reverse('lawyer:waiting'))
    assert [key.lawyer_profile for key in page.context['waiting']] == [newcomer]
    # A lawyer without the group key has nothing to give.
    assert client_for(newcomer).get(reverse('lawyer:waiting')).context['waiting'] == []


# The recipient's browser checks each grant (design §4).

def test_a_grant_that_opens_is_marked_checked(holder):
    grant = KeyGrant.objects.get()
    response = client_for(holder).post(reverse('accounts:grant_checked'), {'grant': grant.pk, 'result': 'passed'})
    assert response.status_code == 204
    grant.refresh_from_db()
    assert grant.checked_at is not None


def test_a_grant_that_fails_is_deleted_and_alerts_the_admin(holder, newcomer):
    old = KeyGrant.objects.get()
    grant = KeyGrant.objects.create(
        group_key=old.group_key, lawyer_key=grants.current_key(newcomer), sealed_private_key=sealed(), granted_by=holder,
    )
    response = client_for(newcomer).post(reverse('accounts:grant_checked'), {'grant': grant.pk, 'result': 'failed'})
    assert response.status_code == 204
    assert not KeyGrant.objects.filter(pk=grant.pk).exists()
    alert = Alert.objects.get()
    assert (alert.kind, alert.account) == (Alert.Kind.KEY_GRANT_FAILED, newcomer.user)
    # Back on the list, for a new grant.
    assert [key.lawyer_profile for key in grants.waiting()] == [newcomer]


def test_only_the_recipient_reports_on_their_grant_and_only_once(holder, newcomer):
    grant = KeyGrant.objects.get()
    client = client_for(newcomer)
    assert client.post(reverse('accounts:grant_checked'), {'grant': grant.pk, 'result': 'failed'}).status_code == 409
    assert client.post(reverse('accounts:grant_checked'), {'grant': 'x', 'result': 'failed'}).status_code == 400
    assert client.post(reverse('accounts:grant_checked'), {'grant': grant.pk, 'result': 'maybe'}).status_code == 400
    holder_client = client_for(holder)
    assert holder_client.post(reverse('accounts:grant_checked'), {'grant': grant.pk, 'result': 'passed'}).status_code == 204
    assert holder_client.post(reverse('accounts:grant_checked'), {'grant': grant.pk, 'result': 'failed'}).status_code == 409
    assert KeyGrant.objects.filter(pk=grant.pk).exists() and not Alert.objects.exists()


def test_a_person_fetches_only_grants_to_their_own_current_key_on_keys_in_use(holder, newcomer):
    group_key(holder, newcomer, status=GroupStatus.REJECTED)
    held = client_for(newcomer).get(reverse('accounts:own_grants')).json()
    assert held == {'grants': [], 'may_start': False}
    assert len(client_for(holder).get(reverse('accounts:own_grants')).json()['grants']) == 1


# The vault serves only lawyers holding a grant (design §3.3).

START = datetime.datetime(2026, 6, 15, 16, tzinfo=datetime.UTC)


@pytest.fixture
def clip(tmp_path):
    centre = grid.cell_for_point(*BASTILLE, 11)
    clip = Clip.objects.create(
        code_hash='1' * 64, status=Clip.Status.READY, stated_time=START + datetime.timedelta(minutes=10),
        duration=datetime.timedelta(seconds=42), stated_cell=centre, stated_precision=Precision.EXACT_SPOT,
    )
    source = tmp_path / 'video.mp4'
    source.write_bytes(b'not really a video')
    vault.record_file(clip, vault.PROXY, vault.store_file(source, 'mp4'))
    vault.record_file(clip, vault.EVIDENCE_COPY, vault.store_file(source, 'mp4'))
    vault.put_text(clip, vault.CONTEXT, 'Filmed from a balcony.')
    return clip


def clip_urls(profile, clip):
    case = make_case(profile, starts_at=START, ends_at=START + datetime.timedelta(hours=1))
    return {name: reverse(f'lawyer:{name}', kwargs={'case_id': case.pk, 'clip_id': clip.pk}) for name in ['clip', 'preview', 'download']}


@pytest.mark.parametrize('kind', ['no grant', 'grant on an old key', 'grant on a rejected key'])
def test_a_lawyer_whose_key_holds_no_grant_gets_nothing_from_the_vault(key_gates, clip, kind):
    lawyer = person('lawyer@example.org')
    if kind == 'grant on an old key':
        group_key(lawyer)
        grants.current_key(lawyer).retire()
        make_key(lawyer)
    elif kind == 'grant on a rejected key':
        group_key(lawyer, status=GroupStatus.REJECTED)
    urls = clip_urls(lawyer, clip)
    client = client_for(lawyer)
    page = client.get(urls['clip'])
    assert page.status_code == 200 and not page.context['reads_vault']
    assert 'balcony' not in page.content.decode() and not page.context['has_proxy']
    assert client.get(urls['preview']).status_code == 404
    assert client.get(urls['download']).status_code == 404
    assert not AuditEntry.objects.filter(action__in=[Action.PREVIEW, Action.DOWNLOAD]).exists()


def test_a_lawyer_holding_a_grant_reads_the_vault(key_gates, clip, holder):
    urls = clip_urls(holder, clip)
    client = client_for(holder)
    assert 'balcony' in client.get(urls['clip']).content.decode()
    assert client.get(urls['preview']).status_code == 200
    assert client.get(urls['download']).status_code == 200


# Uploads are closed while no one could read them (design §5.7, §10).

def test_uploads_are_closed_without_a_group_key(key_gates, client):
    response = client.get(reverse('clips:upload'))
    assert response.status_code == 503
    assert client.post(reverse('clips:upload')).status_code == 503
    original = Original.objects.create()
    assert client.get(reverse('clips:confirm', kwargs={'original_id': original.pk})).status_code == 503


@pytest.mark.parametrize('holder_status, open_', [
    (Status.LAWYER, True), (Status.SUSPENDED, True), (Status.REVOKED, False),
])
def test_uploads_are_open_while_a_lawyer_who_is_not_revoked_holds_the_newest_group_key(key_gates, client, holder_status, open_):
    profile = person('holder@example.org')
    group_key(profile)
    if holder_status != Status.LAWYER:
        profile.change_status(holder_status)
    assert (client.get(reverse('clips:upload')).status_code == 200) == open_


def test_uploads_need_a_holder_of_the_newest_group_key_in_use(key_gates, client):
    profile = person('holder@example.org')
    group_key(profile, status=GroupStatus.RETIRING)
    group_key(status=GroupStatus.CHECKING)
    assert client.get(reverse('clips:upload')).status_code == 503
    group_key(profile, status=GroupStatus.ABANDONED)
    assert client.get(reverse('clips:upload')).status_code == 503
