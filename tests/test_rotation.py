"""Rotations of the group key (docs/m5-encryption-design.md §5.5, §5.4, §5.7; keys.rotation).

The server cannot tell sealed keys from random bytes, so random bytes stand in for what the
rotator's browser seals; the tests drive the server as the browsers would.
"""

import datetime
import json
import os
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import LawyerProfile
from alerts.models import Alert
from audit.models import Action, AuditEntry
from clips import vault
from clips.models import Clip, SealedKey
from keys import grants, rotation
from keys.models import GroupKey, KeyGrant, Rotation, RotationAttempt, RotationCheck
from tests.conftest import log_in_with_second_factor, make_key, with_code_holder_key

pytestmark = pytest.mark.django_db

Status = LawyerProfile.Status
KeyStatus = GroupKey.Status
Slot = RotationCheck.Slot
Because = RotationAttempt.AbandonedBecause
User = get_user_model()


def b64(value):
    import base64
    return base64.b64encode(value).decode()


def lawyer(name, unlocked_days_ago=0):
    profile = LawyerProfile.objects.create(user=User.objects.create_user(f'{name}@example.org'), bar='Paris')
    profile.verified_via, profile.vouched_by = 'The bar directory', 'A defence organisation'
    profile.save()
    profile.change_status(Status.LAWYER)
    key = make_key(profile)
    key.admin_confirmed_at = timezone.now()
    key.last_unlocked_on = timezone.localdate() - datetime.timedelta(days=unlocked_days_ago)
    key.save()
    return profile


def give(group_key, *profiles):
    for profile in profiles:
        KeyGrant.objects.create(
            group_key=group_key, lawyer_key=grants.current_key(profile), sealed_private_key=os.urandom(80),
            granted_by=profile,
        )


@pytest.fixture
def world():
    """Five lawyers holding the current group key, and a clip whose texts are sealed to it."""
    key = GroupKey.objects.create(public_key=os.urandom(32))
    lawyers = [lawyer(name) for name in ('ana', 'ben', 'cleo', 'dev', 'eli')]
    give(key, *lawyers)
    clip = with_code_holder_key(Clip.objects.create(code_hash=uuid.uuid4().hex * 2))
    vault.put_text(clip, vault.CONTEXT, 'Filmed from a balcony.')
    vault.put_text(clip, vault.CONTACT_DETAILS, '@someone')
    return {'key': key, 'lawyers': lawyers, 'clip': clip}


def request_rotation(world, by=None):
    with grants.transaction.atomic():
        grants.lock()
        rotation.request(by or world['lawyers'][0])
    return Rotation.objects.get(ended_at__isnull=True)


def attempt():
    return RotationAttempt.objects.get(ended_at__isnull=True)


def make_new_key(running):
    profile = running.rotator
    sealed = {key.pk: os.urandom(80) for key in rotation.lawyer_keys_to_seal_to(running)}
    return rotation.submit_new_key(profile, os.urandom(32), sealed)


def reseal_all(running):
    batch = rotation.reseal_batch(running.rotator)
    rotation.submit_resealed(running.rotator, {item['id']: os.urandom(80) for item in batch})


def ready_for_checks(world):
    request_rotation(world)
    running = attempt()
    make_new_key(running)
    reseal_all(running)
    running.refresh_from_db()
    return running


def slot_holder(running, slot):
    return running.checks.get(slot=slot, replaced_at__isnull=True).lawyer_profile


def resealed_ids(running):
    return [item['id'] for item in vault.resealed_by(rotation.new_key_of(running), running.rotator)]


# Requests and starting (steps 1 and 2).

def test_revoking_a_lawyer_retires_their_key_deletes_their_grants_and_requests_a_rotation(world, client):
    from accounts.access import revoke_locked
    revoked = world['lawyers'][4]
    request = type('Request', (), {'user': User.objects.create_superuser('admin@example.org')})()
    with grants.transaction.atomic():
        revoke_locked(request, LawyerProfile.objects.get(pk=revoked.pk))
    assert grants.current_key(revoked) is None and not KeyGrant.objects.filter(lawyer_key__lawyer_profile=revoked).exists()
    world['key'].refresh_from_db()
    assert world['key'].status == KeyStatus.ROTATION_REQUESTED
    entry = AuditEntry.objects.get(action=Action.ROTATION_REQUEST)
    assert entry.lawyer_profile == revoked and entry.rotation == Rotation.objects.get()
    # Four eligible lawyers remain: an attempt starts at once, with one of them as rotator.
    assert attempt().rotator in world['lawyers'][:4]


def test_the_rotator_is_drawn_at_random_among_eligible_lawyers_only(world, monkeypatch):
    suspended, stale = world['lawyers'][3], world['lawyers'][4]
    suspended.change_status(Status.SUSPENDED)
    grants.current_key(stale).__class__.objects.filter(lawyer_profile=stale).update(
        last_unlocked_on=timezone.localdate() - datetime.timedelta(days=8),
    )
    pools = []
    real_choice = rotation.secrets.choice
    monkeypatch.setattr(rotation.secrets, 'choice', lambda pool: pools.append(list(pool)) or real_choice(pool))
    request_rotation(world)
    # Not suspended, unlocked within 7 days, holding a grant on the key: compared as people.
    assert pools == [sorted(profile.pk for profile in world['lawyers'][:3])]


def test_no_attempt_starts_with_fewer_than_three_eligible_lawyers_and_the_admin_is_told_after_a_week(world):
    for profile in world['lawyers'][2:]:
        profile.change_status(Status.SUSPENDED)
    rotation_ = request_rotation(world)
    assert not RotationAttempt.objects.exists()
    rotation.tick(now=timezone.now() + datetime.timedelta(days=6))
    assert not Alert.objects.filter(kind=Alert.Kind.ROTATION_NOT_STARTING).exists()
    rotation.tick(now=timezone.now() + datetime.timedelta(days=7, minutes=1))
    assert Alert.objects.filter(kind=Alert.Kind.ROTATION_NOT_STARTING).exists()
    # Once three are eligible again, the next tick starts it.
    world['lawyers'][2].change_status(Status.LAWYER)
    rotation.tick()
    assert attempt().rotation == rotation_


# The new key and resealing (steps 2 and 3).

def test_only_the_rotator_makes_the_new_key_sealed_to_exactly_the_holders_of_the_old(world):
    request_rotation(world)
    running = attempt()
    other = next(profile for profile in world['lawyers'] if profile != running.rotator)
    to = {key.pk: os.urandom(80) for key in rotation.lawyer_keys_to_seal_to(running)}
    with pytest.raises(rotation.Refused):
        rotation.submit_new_key(other, os.urandom(32), to)
    missing_one = dict(list(to.items())[1:])
    with pytest.raises(rotation.Refused):
        rotation.submit_new_key(running.rotator, os.urandom(32), missing_one)
    new = rotation.submit_new_key(running.rotator, os.urandom(32), to)
    world['key'].refresh_from_db()
    assert (new.status, world['key'].status, new.attempt) == (KeyStatus.CHECKING, KeyStatus.RETIRING, running)
    assert set(new.grants.values_list('lawyer_key', flat=True)) == set(to)
    with pytest.raises(rotation.Refused):
        rotation.submit_new_key(running.rotator, os.urandom(32), to)
    assert AuditEntry.objects.filter(action=Action.ROTATION_NEW_KEY, lawyer_profile=running.rotator).count() == 1


def test_resealing_never_replaces_a_key_the_server_sealed_and_ends_with_two_distinct_checkers(world):
    request_rotation(world)
    running = attempt()
    new = make_new_key(running)
    # Written during the rotation: the server seals it to both keys itself.
    vault.put_text(world['clip'], vault.CONTEXT, 'Edited during the rotation.')
    server_sealed = SealedKey.objects.get(vault_text__kind=vault.CONTEXT, group_key=new)
    batch = rotation.reseal_batch(running.rotator)
    assert len(batch) == 1  # The contact details only.
    stored = rotation.submit_resealed(running.rotator, {item['id']: os.urandom(80) for item in batch})
    assert stored == 1
    server_sealed.refresh_from_db()
    assert server_sealed.sealed_by is None
    assert SealedKey.objects.get(vault_text__kind=vault.CONTACT_DETAILS, group_key=new).sealed_by == running.rotator
    running.refresh_from_db()
    first, second = slot_holder(running, Slot.FIRST), slot_holder(running, Slot.SECOND)
    assert running.resealed_at is not None and len({first.pk, second.pk, running.rotator.pk}) == 3


def test_a_rotator_out_of_time_abandons_the_attempt_and_a_new_one_is_drawn(world):
    request_rotation(world)
    running = attempt()
    new = make_new_key(running)
    rotation.tick(now=timezone.now() + datetime.timedelta(days=3, minutes=1))
    running.refresh_from_db()
    new.refresh_from_db()
    world['key'].refresh_from_db()
    assert (running.outcome, running.abandoned_because) == ('abandoned', Because.ROTATOR_TIME)
    assert new.status == KeyStatus.ABANDONED and not new.grants.exists() and not new.sealed_keys.exists()
    assert world['key'].status == KeyStatus.ROTATION_REQUESTED
    assert Alert.objects.filter(kind=Alert.Kind.ROTATOR_TIME, account=running.rotator.user).exists()
    assert attempt() != running


# Checks (steps 4 to 9).

def test_the_old_sealed_keys_go_only_after_two_passes_by_two_lawyers_other_than_the_rotator(world):
    running = ready_for_checks(world)
    old_sealed = SealedKey.objects.filter(group_key=world['key'])
    assert old_sealed.exists()
    rotation.report(slot_holder(running, Slot.FIRST), True)
    assert old_sealed.exists()
    with pytest.raises(rotation.Refused):
        rotation.report(running.rotator, True)
    rotation.report(slot_holder(running, Slot.SECOND), True)
    world['key'].refresh_from_db()
    new = rotation.new_key_of(running)
    new.refresh_from_db()
    assert not old_sealed.exists() and not world['key'].grants.exists()
    assert (world['key'].status, new.status) == (KeyStatus.RETIRED, KeyStatus.CURRENT)
    assert Rotation.objects.get().ended_at is not None
    assert AuditEntry.objects.filter(action=Action.ROTATION_END).count() == 1
    # The texts are still readable to the code holder, and sealed to the new key.
    assert vault.code_holder_text(world['clip'], vault.CONTEXT, '7K3QM9XA2B4CD6EF8GHJ0KMN1PQR') == 'Filmed from a balcony.'
    assert SealedKey.objects.filter(group_key=new, vault_text__isnull=False).count() == 2


def test_a_file_recorded_during_a_rotation_stays_readable_whichever_way_it_ends(world, tmp_path):
    running = ready_for_checks(world)
    source = tmp_path / 'proxy.mp4'
    source.write_bytes(b'recorded during the rotation')
    name, data_key = vault.store_file(world['clip'], vault.PROXY, source, 'mp4')
    vault.record_file(world['clip'], vault.PROXY, name, data_key)
    sealed_to = set(SealedKey.objects.filter(vault_file__isnull=False).values_list('group_key', flat=True))
    assert sealed_to == {world['key'].pk, rotation.new_key_of(running).pk}
    # Rejected: the old key's copy stays.
    rotation.report(slot_holder(running, Slot.FIRST), False, resealed_ids(running))
    rotation.report(slot_holder(running, Slot.SECOND), False, resealed_ids(running))
    assert SealedKey.objects.filter(vault_file__isnull=False, group_key=world['key']).exists()


def test_two_failures_reject_the_attempt_at_once(world):
    running = ready_for_checks(world)
    new = rotation.new_key_of(running)
    rotation.report(slot_holder(running, Slot.FIRST), False, resealed_ids(running))
    rotation.report(slot_holder(running, Slot.SECOND), False, resealed_ids(running))
    running.refresh_from_db()
    new.refresh_from_db()
    world['key'].refresh_from_db()
    assert running.outcome == 'rejected' and new.status == KeyStatus.REJECTED
    assert not new.grants.exists() and not new.sealed_keys.exists()
    assert world['key'].status == KeyStatus.ROTATION_REQUESTED and SealedKey.objects.filter(group_key=world['key']).exists()
    assert Alert.objects.filter(kind=Alert.Kind.ROTATION_REJECTED).exists()
    assert AuditEntry.objects.filter(action=Action.ROTATION_REJECTION, rotation_attempt=running).count() == 1


def test_one_failure_is_rejected_only_once_a_confirmer_outside_both_slots_agrees(world):
    running = ready_for_checks(world)
    failing = slot_holder(running, Slot.FIRST)
    rotation.report(failing, False, resealed_ids(running)[:1])
    running.refresh_from_db()
    assert running.ended_at is None
    confirmer = slot_holder(running, Slot.CONFIRMING)
    assert confirmer.pk not in {running.rotator.pk, failing.pk, slot_holder(running, Slot.SECOND).pk}
    _, items = rotation.to_check(confirmer)
    assert [item['id'] for item in items] == resealed_ids(running)[:1]
    rotation.report(confirmer, False, resealed_ids(running)[:1])
    running.refresh_from_db()
    assert running.outcome == 'rejected'


def test_a_confirmer_finding_the_items_sound_abandons_the_attempt_as_disputed(world):
    running = ready_for_checks(world)
    failing = slot_holder(running, Slot.FIRST)
    rotation.report(failing, False, resealed_ids(running))
    confirmer = slot_holder(running, Slot.CONFIRMING)
    rotation.report(confirmer, True)
    running.refresh_from_db()
    assert (running.outcome, running.abandoned_because) == ('abandoned', Because.DISPUTED)
    named = set(Alert.objects.filter(kind=Alert.Kind.ROTATION_DISPUTED).values_list('account', flat=True))
    assert named == {failing.user.pk, confirmer.user.pk}
    # The old sealed keys stay, and a new attempt is drawn.
    assert SealedKey.objects.filter(group_key=world['key']).exists() and attempt() != running


def test_a_failure_no_one_can_confirm_abandons_the_attempt_and_names_the_checker(world):
    running = ready_for_checks(world)
    # Only the rotator and both checkers are left eligible.
    for profile in world['lawyers']:
        if profile.pk not in {running.rotator.pk, slot_holder(running, Slot.FIRST).pk, slot_holder(running, Slot.SECOND).pk}:
            profile.change_status(Status.SUSPENDED)
    failing = slot_holder(running, Slot.FIRST)
    rotation.report(failing, False, resealed_ids(running))
    running.refresh_from_db()
    assert (running.outcome, running.abandoned_because) == ('abandoned', Because.NO_ONE_ELIGIBLE)
    assert Alert.objects.filter(kind=Alert.Kind.ROTATION_NO_ONE_ELIGIBLE, account=failing.user).exists()
    # The three still eligible on the old key are enough for a fresh draw: nothing is stuck.
    assert attempt() != running and attempt().rotator.pk in {
        running.rotator.pk, failing.pk, slot_holder(running, Slot.SECOND).pk,
    }


def test_a_slot_without_a_result_after_three_days_is_drawn_again_with_an_alert(world):
    running = ready_for_checks(world)
    idle = slot_holder(running, Slot.SECOND)
    rotation.report(slot_holder(running, Slot.FIRST), True)
    rotation.tick(now=timezone.now() + datetime.timedelta(days=3, minutes=1))
    replacement = slot_holder(running, Slot.SECOND)
    assert replacement != idle and replacement != running.rotator
    assert Alert.objects.filter(kind=Alert.Kind.ROTATION_ROLE_REDRAWN, account=idle.user).exists()
    with pytest.raises(rotation.Refused):
        rotation.report(idle, True)
    rotation.report(replacement, True)
    assert rotation.new_key_of(running).status == KeyStatus.CURRENT


def test_a_suspended_checker_loses_their_slot_even_after_passing(world):
    from accounts.access import change_access  # noqa: F401 - the path a suspension takes
    running = ready_for_checks(world)
    passed = slot_holder(running, Slot.FIRST)
    rotation.report(passed, True)
    with grants.transaction.atomic():
        passed.change_status(Status.SUSPENDED)
        grants.lock()
        rotation.suspended(passed)
    assert slot_holder(running, Slot.FIRST) != passed
    rotation.report(slot_holder(running, Slot.SECOND), True)
    # One pass left, from the second slot: the attempt has not ended.
    running.refresh_from_db()
    assert running.ended_at is None


def test_a_suspended_rotator_still_resealing_abandons_the_attempt(world):
    request_rotation(world)
    running = attempt()
    make_new_key(running)
    with grants.transaction.atomic():
        running.rotator.change_status(Status.SUSPENDED)
        grants.lock()
        rotation.suspended(running.rotator)
    running.refresh_from_db()
    assert (running.outcome, running.abandoned_because) == ('abandoned', Because.ROTATOR_SUSPENDED)


def test_a_revocation_during_a_rotation_abandons_the_running_attempt(world):
    running = ready_for_checks(world)
    request_rotation(world, by=world['lawyers'][4])
    running.refresh_from_db()
    assert (running.outcome, running.abandoned_because) == ('abandoned', Because.NEW_REQUEST)
    assert rotation.new_key_of(running).status == KeyStatus.ABANDONED


def test_a_grant_on_the_new_key_reported_as_failing_is_kept(world):
    running = ready_for_checks(world)
    holder = slot_holder(running, Slot.FIRST)
    grant = KeyGrant.objects.get(group_key=rotation.new_key_of(running), lawyer_key=grants.current_key(holder))
    grants.report_check(holder, grant.pk, bytes(grant.lawyer_key.public_key), False)
    assert KeyGrant.objects.filter(pk=grant.pk).exists()


# The views the browsers use (step 9 builds the browser side).

def client_for(profile):
    return log_in_with_second_factor(Client(), profile.user)


def test_the_rotation_views_walk_an_attempt_to_its_end(world):
    request_rotation(world)
    running = attempt()
    rotator = client_for(running.rotator)
    role = rotator.get(reverse('lawyer:rotation_role')).json()
    assert role['role'] == 'rotator' and role['stage'] == 'new_key' and len(role['seal_to']) == 5
    sealed = {str(item['lawyer_key']): b64(os.urandom(80)) for item in role['seal_to']}
    assert rotator.post(reverse('lawyer:rotation_new_key'), {
        'public_key': b64(os.urandom(32)), 'grants': json.dumps(sealed),
    }).status_code == 201
    items = rotator.get(reverse('lawyer:rotation_reseal')).json()['items']
    resealed = {str(item['id']): b64(os.urandom(80)) for item in items}
    assert rotator.post(reverse('lawyer:rotation_resealed'), {'resealed': json.dumps(resealed)}).json() == {'stored': 2}
    running.refresh_from_db()
    for slot in (Slot.FIRST, Slot.SECOND):
        checker = client_for(slot_holder(running, slot))
        assert checker.get(reverse('lawyer:rotation_role')).json()['role'] == 'checker'
        assert len(checker.get(reverse('lawyer:rotation_to_check')).json()['items']) == 2
        assert checker.post(reverse('lawyer:rotation_checked'), {'result': 'passed'}).status_code == 204
    assert rotation.new_key_of(running).status == KeyStatus.CURRENT


def test_the_rotation_views_are_for_lawyers_holding_the_group_key(world):
    request_rotation(world)
    newcomer = lawyer('fay')
    client = client_for(newcomer)
    for name in ('lawyer:rotation_role', 'lawyer:rotation_reseal', 'lawyer:rotation_to_check'):
        assert client.get(reverse(name)).status_code == 404
    other = next(profile for profile in world['lawyers'] if profile != attempt().rotator)
    assert client_for(other).get(reverse('lawyer:rotation_reseal')).status_code == 409


def test_the_revoke_page_warns_before_revoking_the_last_holder(settings):
    settings.LANGUAGE_CODE = 'en'
    key = GroupKey.objects.create(public_key=os.urandom(32))
    only = lawyer('only')
    give(key, only)
    admin = log_in_with_second_factor(Client(), User.objects.create_superuser('admin@example.org'))
    page = admin.get(reverse('manage:revoke', args=[only.pk])).content.decode()
    assert 'the last lawyer holding the group key' in page
    give(key, lawyer('second'))
    assert 'the last lawyer holding the group key' not in admin.get(reverse('manage:revoke', args=[only.pk])).content.decode()


def test_a_confirmer_redrawn_after_three_days_checks_the_same_items(world):
    lawyers = world['lawyers'] + [lawyer('fay')]
    give(world['key'], lawyers[-1])
    running = ready_for_checks(world)
    rotation.report(slot_holder(running, Slot.FIRST), False, resealed_ids(running)[:1])
    idle = slot_holder(running, Slot.CONFIRMING)
    rotation.tick(now=timezone.now() + datetime.timedelta(days=3, minutes=1))
    replacement = slot_holder(running, Slot.CONFIRMING)
    assert replacement != idle and replacement.pk not in {running.rotator.pk, slot_holder(running, Slot.FIRST).pk}
    assert [item['id'] for item in rotation.to_check(replacement)[1]] == resealed_ids(running)[:1]
    assert Alert.objects.filter(kind=Alert.Kind.ROTATION_ROLE_REDRAWN, account=idle.user).exists()


def test_drawing_checkers_that_finds_no_one_abandons_the_attempt(world):
    request_rotation(world)
    running = attempt()
    make_new_key(running)
    # Everyone but the rotator and one other drops out before resealing ends.
    others = [profile for profile in world['lawyers'] if profile != running.rotator]
    for profile in others[1:]:
        profile.change_status(Status.SUSPENDED)
    reseal_all(running)
    running.refresh_from_db()
    assert (running.outcome, running.abandoned_because) == ('abandoned', Because.NO_ONE_ELIGIBLE)
    assert Alert.objects.filter(kind=Alert.Kind.ROTATION_NO_ONE_ELIGIBLE, account=None).exists()
