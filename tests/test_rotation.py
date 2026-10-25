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
from nacl.public import PrivateKey

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


def report(profile, passed, failed_items=()):
    """A result naming the running attempt's new key, as the browser sends it."""
    running = rotation.running_attempt()
    new = running and rotation.new_key_of(running)
    return rotation.report(profile, passed, failed_items, bytes(new.public_key) if new else bytes(32))


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
    report(slot_holder(running, Slot.FIRST), True)
    assert old_sealed.exists()
    with pytest.raises(rotation.Refused):
        report(running.rotator, True)
    report(slot_holder(running, Slot.SECOND), True)
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
    report(slot_holder(running, Slot.FIRST), False, resealed_ids(running))
    report(slot_holder(running, Slot.SECOND), False, resealed_ids(running))
    assert SealedKey.objects.filter(vault_file__isnull=False, group_key=world['key']).exists()


def test_two_failures_reject_the_attempt_at_once(world):
    running = ready_for_checks(world)
    new = rotation.new_key_of(running)
    report(slot_holder(running, Slot.FIRST), False, resealed_ids(running))
    report(slot_holder(running, Slot.SECOND), False, resealed_ids(running))
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
    report(failing, False, resealed_ids(running)[:1])
    running.refresh_from_db()
    assert running.ended_at is None
    confirmer = slot_holder(running, Slot.CONFIRMING)
    assert confirmer.pk not in {running.rotator.pk, failing.pk, slot_holder(running, Slot.SECOND).pk}
    _, items, _ = rotation.to_check(confirmer)
    assert [item['id'] for item in items] == resealed_ids(running)[:1]
    report(confirmer, False, resealed_ids(running)[:1])
    running.refresh_from_db()
    assert running.outcome == 'rejected'


def test_a_confirmer_finding_the_items_sound_abandons_the_attempt_as_disputed(world):
    running = ready_for_checks(world)
    failing = slot_holder(running, Slot.FIRST)
    report(failing, False, resealed_ids(running))
    confirmer = slot_holder(running, Slot.CONFIRMING)
    report(confirmer, True)
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
    report(failing, False, resealed_ids(running))
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
    report(slot_holder(running, Slot.FIRST), True)
    rotation.tick(now=timezone.now() + datetime.timedelta(days=3, minutes=1))
    replacement = slot_holder(running, Slot.SECOND)
    assert replacement != idle and replacement != running.rotator
    assert Alert.objects.filter(kind=Alert.Kind.ROTATION_ROLE_REDRAWN, account=idle.user).exists()
    with pytest.raises(rotation.Refused):
        report(idle, True)
    report(replacement, True)
    assert rotation.new_key_of(running).status == KeyStatus.CURRENT


def test_a_suspended_checker_loses_their_slot_even_after_passing(world):
    from accounts.access import change_access  # noqa: F401 - the path a suspension takes
    running = ready_for_checks(world)
    passed = slot_holder(running, Slot.FIRST)
    report(passed, True)
    with grants.transaction.atomic():
        passed.change_status(Status.SUSPENDED)
        grants.lock()
        rotation.suspended(passed)
    assert slot_holder(running, Slot.FIRST) != passed
    report(slot_holder(running, Slot.SECOND), True)
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


# The views the browsers use (keys/static/keys/rotation.js).

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
        # A real key: the form refuses what is not one.
        'public_key': b64(bytes(PrivateKey.generate().public_key)), 'grants': json.dumps(sealed),
    }).status_code == 201
    items = rotator.get(reverse('lawyer:rotation_reseal')).json()['items']
    resealed = {str(item['id']): b64(os.urandom(80)) for item in items}
    assert rotator.post(reverse('lawyer:rotation_resealed'), {'resealed': json.dumps(resealed)}).json() == {'stored': 2}
    running.refresh_from_db()
    for slot in (Slot.FIRST, Slot.SECOND):
        checker = client_for(slot_holder(running, slot))
        role = checker.get(reverse('lawyer:rotation_role')).json()
        # The old key too, to try what the rotator said they could not open.
        assert role == {
            'role': 'checker', 'old_public_key': b64(bytes(world['key'].public_key)),
            'new_public_key': b64(bytes(rotation.new_key_of(running).public_key)),
        }
        assert len(checker.get(reverse('lawyer:rotation_to_check')).json()['items']) == 2
        assert checker.post(reverse('lawyer:rotation_checked'), {
            'result': 'passed', 'new_public_key': role['new_public_key'],
        }).status_code == 204
    assert rotation.new_key_of(running).status == KeyStatus.CURRENT


def test_a_result_for_another_attempts_key_is_refused(world):
    """A page left open during an earlier attempt, whose lawyer was drawn again: its result
    names the earlier key, and must not count as a check of this one."""
    running = ready_for_checks(world)
    checker = slot_holder(running, Slot.FIRST)
    response = client_for(checker).post(reverse('lawyer:rotation_checked'), {
        'result': 'failed', 'new_public_key': b64(bytes(PrivateKey.generate().public_key)),
    })
    assert response.status_code == 409
    assert running.checks.get(slot=Slot.FIRST, replaced_at__isnull=True).result == RotationCheck.Result.NONE


def test_the_rotation_views_are_for_lawyers_holding_the_group_key(world):
    request_rotation(world)
    newcomer = lawyer('fay')
    client = client_for(newcomer)
    for name in ('lawyer:rotation_role', 'lawyer:rotation_reseal', 'lawyer:rotation_to_check'):
        assert client.get(reverse(name)).status_code == 404
    other = next(profile for profile in world['lawyers'] if profile != attempt().rotator)
    assert client_for(other).get(reverse('lawyer:rotation_reseal')).status_code == 409


def test_lawyers_pages_take_part_in_rotations(world):
    """Every page that unlocks the key runs the rotation script, with where it reports: a role
    is carried out at the lawyer's next vault page, whichever it is (design §5.5)."""
    page = client_for(world['lawyers'][0]).get(reverse('lawyer:home')).content.decode()
    assert 'keys/rotation.js' in page and 'id="rotation"' in page
    for name in ('rotation_role', 'rotation_new_key', 'rotation_reseal', 'rotation_resealed', 'rotation_unopenable',
                 'rotation_to_check', 'rotation_checked'):
        assert f'"{reverse("lawyer:" + name)}"' in page


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
    report(slot_holder(running, Slot.FIRST), False, resealed_ids(running)[:1])
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


# From the review: revocation always rotates; keys that cannot serve; a confirmer goes with the
# failure; abandons alert; the audit log keeps who found a failure.

def revoke(profile):
    from accounts.access import revoke_locked
    request = type('Request', (), {'user': User.objects.get_or_create(email='admin@example.org', defaults={
        'is_staff': True, 'is_superuser': True,
    })[0]})()
    with grants.transaction.atomic():
        revoke_locked(request, LawyerProfile.objects.get(pk=profile.pk))


def test_revoking_a_lawyer_whose_grant_is_gone_still_requests_a_rotation(world):
    revoked = world['lawyers'][4]
    # They had the group key, and had their grant deleted by reporting it as failing.
    KeyGrant.objects.filter(lawyer_key__lawyer_profile=revoked).delete()
    revoke(revoked)
    world['key'].refresh_from_db()
    assert world['key'].status == KeyStatus.ROTATION_REQUESTED and Rotation.objects.exists()


@pytest.mark.parametrize('public_key', ['zeros', 'the old key', 'the old key with its top bit set'])
def test_a_new_key_that_could_not_serve_is_refused(world, public_key):
    request_rotation(world)
    running = attempt()
    client = client_for(running.rotator)
    role = client.get(reverse('lawyer:rotation_role')).json()
    sealed = {str(item['lawyer_key']): b64(os.urandom(80)) for item in role['seal_to']}
    value = bytes(32) if public_key == 'zeros' else bytes(world['key'].public_key)
    if public_key == 'the old key with its top bit set':
        # X25519 ignores that bit: the same key, written otherwise.
        value = value[:31] + bytes([value[31] | 0x80])
    response = client.post(reverse('lawyer:rotation_new_key'), {'public_key': b64(value), 'grants': json.dumps(sealed)})
    assert response.status_code in (400, 409) and rotation.new_key_of(running) is None


def test_a_confirmer_goes_with_the_failure_once_its_checker_is_replaced(world, monkeypatch):
    lawyers = world['lawyers'] + [lawyer('fay'), lawyer('gus')]
    give(world['key'], *lawyers[5:])
    running = ready_for_checks(world)
    failing = slot_holder(running, Slot.FIRST)
    report(failing, False, resealed_ids(running))
    confirmer = slot_holder(running, Slot.CONFIRMING)
    # Once their role ends, the former confirmer may be drawn for the new slot (design §5.5):
    # kept out here, so that what they report can only be about the check that ended.
    monkeypatch.setattr(rotation, 'draw', lambda pool: LawyerProfile.objects.get(pk=min(set(pool) - {confirmer.pk})))
    with grants.transaction.atomic():
        failing.change_status(Status.SUSPENDED)
        grants.lock()
        rotation.suspended(failing)
    assert not running.checks.filter(slot=Slot.CONFIRMING, replaced_at__isnull=True).exists()
    with pytest.raises(rotation.Refused):
        report(confirmer, True)
    # The new checker's failure is confirmed afresh.
    report(slot_holder(running, Slot.FIRST), False, resealed_ids(running)[:1])
    assert running.checks.filter(slot=Slot.CONFIRMING, replaced_at__isnull=True).exists()
    running.refresh_from_db()
    assert running.ended_at is None


@pytest.mark.parametrize('cause', ['new request', 'rotator suspended'])
def test_every_abandon_but_starting_over_alerts_the_admin(world, cause):
    request_rotation(world)
    running = attempt()
    make_new_key(running)
    if cause == 'new request':
        request_rotation(world, by=world['lawyers'][4])
    else:
        with grants.transaction.atomic():
            running.rotator.change_status(Status.SUSPENDED)
            grants.lock()
            rotation.suspended(running.rotator)
    assert Alert.objects.filter(kind=Alert.Kind.ROTATION_ABANDONED, account=running.rotator.user).exists()


def test_the_audit_log_keeps_who_passed_and_who_found_a_failure(world):
    running = ready_for_checks(world)
    first, second = slot_holder(running, Slot.FIRST), slot_holder(running, Slot.SECOND)
    report(first, False, resealed_ids(running))
    report(second, False, resealed_ids(running))
    entries = AuditEntry.objects.filter(rotation_attempt=running)
    assert set(entries.filter(action=Action.ROTATION_CHECK_FAILED).values_list('lawyer_profile', flat=True)) == {first.pk, second.pk}
    assert entries.get(action=Action.ROTATION_REJECTION).lawyer_profile == running.rotator
    assert entries.get(action=Action.ROTATION_ATTEMPT).lawyer_profile == running.rotator


@pytest.mark.parametrize('name, data', [
    ('lawyer:rotation_reseal', {'after': '9' * 5000}),
])
def test_numbers_too_long_to_be_ids_are_refused(world, name, data):
    request_rotation(world)
    assert client_for(attempt().rotator).get(reverse(name), data).status_code == 400


def test_a_check_with_numbers_too_long_to_be_ids_is_refused(world):
    running = ready_for_checks(world)
    client = client_for(slot_holder(running, Slot.FIRST))
    response = client.post(reverse('lawyer:rotation_checked'), {'result': 'failed', 'failed_items': '[' + '9' * 5000 + ']'})
    assert response.status_code == 400


def test_a_check_nested_too_deep_to_read_is_refused(world):
    running = ready_for_checks(world)
    client = client_for(slot_holder(running, Slot.FIRST))
    response = client.post(reverse('lawyer:rotation_checked'), {'result': 'failed', 'failed_items': '[' * 100_000})
    assert response.status_code == 400


# Data keys no one can open (design §5.5): the rotator says so, the checkers check the claim.

def reported_unopenable(world):
    request_rotation(world)
    running = attempt()
    make_new_key(running)
    batch = rotation.reseal_batch(running.rotator)
    unopenable, rest = batch[0]['id'], batch[1:]
    # An id that is not one to reseal is ignored.
    rotation.report_unopenable(running.rotator, [unopenable, 10 ** 12])
    rotation.submit_resealed(running.rotator, {item['id']: os.urandom(80) for item in rest})
    running.refresh_from_db()
    return running, unopenable


def test_resealing_goes_on_without_what_the_rotator_cannot_open_and_the_checkers_see_it(world):
    running, unopenable = reported_unopenable(world)
    assert running.unopenable_items == [unopenable] and running.resealed_at is not None
    _, resealed, claimed = rotation.to_check(slot_holder(running, Slot.FIRST))
    assert [item['id'] for item in claimed] == [unopenable] and len(resealed) == 1


def test_an_item_no_one_can_open_loses_its_old_copy_at_the_end_and_the_admin_is_told(world, caplog):
    running, unopenable = reported_unopenable(world)
    for slot in (Slot.FIRST, Slot.SECOND):
        report(slot_holder(running, slot), True)
    assert rotation.new_key_of(running).status == KeyStatus.CURRENT
    assert not SealedKey.objects.filter(pk=unopenable).exists()
    assert Alert.objects.filter(kind=Alert.Kind.ROTATION_UNOPENABLE_ITEMS).exists()
    assert str(world['clip'].pk) in caplog.text


def test_a_false_claim_that_an_item_cannot_be_opened_is_a_failed_check(world):
    running, unopenable = reported_unopenable(world)
    # Both checkers opened it with the old key: the rotator's claim was false.
    report(slot_holder(running, Slot.FIRST), False, [unopenable])
    report(slot_holder(running, Slot.SECOND), False, [unopenable])
    running.refresh_from_db()
    assert running.outcome == 'rejected' and SealedKey.objects.filter(pk=unopenable).exists()
