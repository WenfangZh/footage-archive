"""Rotations of the group key (docs/m5-encryption-design.md §5.5).

A revocation, or a lawyer's report that their key may have been exposed, requests a rotation:
the group key in use is replaced, and every data key resealed to the new one, so that nothing
in the database opens with the old one any more. A browser does the cryptography; this module
decides who does what, and when.

Every role goes to a lawyer drawn at random (secrets), never to whoever arrives first:

1. a rotator, among at least three lawyers eligible on the old key, makes the new key, seals it
   to every lawyer key holding the old one, and reseals the data keys;
2. once nothing is left to reseal, two checkers check the rotator's work;
3. a single reported failure is checked again by a confirmer, outside the rotator and both
   checkers: if they agree the attempt is rejected, otherwise it is abandoned as disputed.

Two passes end the rotation; a rejection or abandonment returns the old key to waiting for a
new attempt. A role left undone for ROTATION_ROLE_DAYS is drawn again (a checker's or
confirmer's) or ends the attempt (the rotator's). Lawyers are compared as people, never as
keys. Every change here holds the group key lock (keys.grants.lock).
"""

import datetime
import logging
import secrets

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.models import LawyerProfile
from alerts.checks import raise_alert
from alerts.models import Alert
from audit.models import Action, record
from clips import vault

from . import grants
from .models import GroupKey, KeyGrant, LawyerKey, Rotation, RotationAttempt, RotationCheck

logger = logging.getLogger(__name__)
Status = GroupKey.Status
Slot = RotationCheck.Slot
Result = RotationCheck.Result
Because = RotationAttempt.AbandonedBecause
# Enough for a rotator and two checkers (design §5.5).
MIN_ELIGIBLE = 3
# The key in use before any rotation running, or being replaced by one.
ESTABLISHED = [Status.CURRENT, Status.ROTATION_REQUESTED, Status.RETIRING]


class Refused(Exception):
    """The request does not match the rotation as it is now."""


def role_days():
    return datetime.timedelta(days=settings.ROTATION_ROLE_DAYS)


def running():
    return Rotation.objects.filter(ended_at__isnull=True).select_related('group_key').first()


def running_attempt():
    return RotationAttempt.objects.filter(ended_at__isnull=True).select_related('rotation__group_key').first()


def holders(attempt):
    """The lawyers holding an unreplaced role in the attempt: its rotator and its checkers."""
    return {attempt.rotator_id} | set(
        attempt.checks.filter(replaced_at__isnull=True).values_list('lawyer_profile_id', flat=True)
    )


def eligible(group_key, attempt=None, also_excluding=()):
    """Lawyers eligible for a draw (design §5.5): their current key holds a grant on
    `group_key`, they are not suspended, they unlocked their key within ROTATION_UNLOCK_DAYS,
    and they hold no other role in the attempt."""
    since = timezone.localdate() - datetime.timedelta(days=settings.ROTATION_UNLOCK_DAYS)
    keys = LawyerKey.objects.current().filter(
        grants__group_key=group_key, lawyer_profile__status=LawyerProfile.Status.LAWYER, last_unlocked_on__gte=since,
    )
    excluded = set(also_excluding) | (holders(attempt) if attempt else set())
    return sorted(set(keys.values_list('lawyer_profile_id', flat=True)) - excluded)


def draw(pool):
    return LawyerProfile.objects.get(pk=secrets.choice(pool))


# Requests (step 1).

def request(lawyer_profile):
    """A revocation of `lawyer_profile`, or their report of an exposed key, requests a rotation of
    the group key in use; during a rotation, the attempt running is abandoned, since they may
    already hold its new key. Call under the lock."""
    established = GroupKey.objects.filter(status__in=ESTABLISHED).first()
    if established is None:
        return
    rotation = running()
    if rotation is None:
        rotation = Rotation.objects.create(group_key=established)
        established.status = Status.ROTATION_REQUESTED
        established.save(update_fields=['status'])
    else:
        attempt = running_attempt()
        if attempt is not None:
            abandon(attempt, Because.NEW_REQUEST)
    record(Action.ROTATION_REQUEST, lawyer_profile, rotation=rotation)
    start(rotation)


# Starting an attempt (step 2).

def start(rotation, now=None):
    """Draw a rotator for the rotation, if no attempt is running and enough lawyers are eligible;
    otherwise, once it has waited ROTATION_START_ALERT_DAYS, alert the admin."""
    now = now or timezone.now()
    if rotation.ended_at is not None or rotation.attempts.filter(ended_at__isnull=True).exists():
        return None
    pool = eligible(rotation.group_key)
    if len(pool) < MIN_ELIGIBLE:
        last_try = rotation.attempts.order_by('-ended_at').values_list('ended_at', flat=True).first() or rotation.requested_at
        if now - last_try >= datetime.timedelta(days=settings.ROTATION_START_ALERT_DAYS):
            raise_alert(Alert.Kind.ROTATION_NOT_STARTING)
        return None
    attempt = RotationAttempt.objects.create(rotation=rotation, rotator=draw(pool), drawn_at=now)
    record(Action.ROTATION_ATTEMPT, attempt.rotator, rotation_attempt=attempt)
    return attempt


# The rotator: a new key (step 2) and resealing (step 3).

def rotators_attempt(profile):
    """The running attempt `profile` is the rotator of, or Refused."""
    attempt = running_attempt()
    if attempt is None or attempt.rotator_id != profile.pk or profile.status != LawyerProfile.Status.LAWYER:
        raise Refused
    return attempt


def new_key_of(attempt):
    return GroupKey.objects.filter(attempt=attempt).first()


def lawyer_keys_to_seal_to(attempt):
    """The lawyer keys holding a grant on the old key: the new key is sealed to each, suspended
    lawyers' included, so that reinstatement needs nothing new."""
    return LawyerKey.objects.filter(grants__group_key=attempt.rotation.group_key).distinct()


def submit_new_key(profile, public_key, sealed):
    """The rotator's new group key, and its grants: `sealed` maps lawyer key ids to the new
    private key sealed to each, and must cover exactly the keys holding the old one."""
    with transaction.atomic():
        grants.lock()
        profile = LawyerProfile.objects.get(pk=profile.pk)
        attempt = rotators_attempt(profile)
        to = {key.pk: key for key in lawyer_keys_to_seal_to(attempt)}
        if new_key_of(attempt) is not None or set(sealed) != set(to):
            raise Refused
        # Never a key the archive has held: the old one would still open everything, for the very
        # lawyer whose revocation this rotation answers.
        if GroupKey.objects.filter(public_key=public_key).exists():
            raise Refused
        old = attempt.rotation.group_key
        new = GroupKey.objects.create(public_key=public_key, status=Status.CHECKING, attempt=attempt)
        own = grants.current_key(profile)
        for key_id, sealed_key in sealed.items():
            KeyGrant.objects.create(
                group_key=new, lawyer_key=to[key_id], sealed_private_key=sealed_key, granted_by=profile,
                # The rotator's own browser made it.
                checked_at=timezone.now() if own and key_id == own.pk else None,
            )
        old.status = Status.RETIRING
        old.save(update_fields=['status'])
        record(Action.ROTATION_NEW_KEY, profile, rotation_attempt=attempt)
        finish_resealing_if_done(attempt)
    return new


def reseal_batch(profile, after=0):
    """The next data keys sealed to the old key, for the rotator's browser to reseal."""
    attempt = rotators_attempt(profile)
    new = new_key_of(attempt)
    if new is None or attempt.resealed_at is not None:
        raise Refused
    return vault.to_reseal(attempt.rotation.group_key, new, after=after, skipped=attempt.unopenable_items)


def report_unopenable(profile, ids):
    """The rotator's browser could not open these data keys sealed to the old key: resealing goes
    on without them, and the checkers check the claim (design §5.5). Every lawyer holds the same
    group key, so one that does not open for the rotator opens for no one."""
    with transaction.atomic():
        grants.lock()
        profile = LawyerProfile.objects.get(pk=profile.pk)
        attempt = rotators_attempt(profile)
        new = new_key_of(attempt)
        if new is None or attempt.resealed_at is not None:
            raise Refused
        found = vault.still_to_reseal(attempt.rotation.group_key, new, ids)
        attempt.unopenable_items = sorted(set(attempt.unopenable_items) | found)
        attempt.save(update_fields=['unopenable_items'])
        finish_resealing_if_done(attempt)


def submit_resealed(profile, resealed):
    """Store the rotator's resealed data keys: `resealed` maps old sealed key ids to the data key
    sealed to the new group key. Returns how many were stored."""
    with transaction.atomic():
        grants.lock()
        profile = LawyerProfile.objects.get(pk=profile.pk)
        attempt = rotators_attempt(profile)
        new = new_key_of(attempt)
        if new is None or attempt.resealed_at is not None:
            raise Refused
        stored = sum(
            vault.store_resealed(old_id, attempt.rotation.group_key, new, sealed_key, profile)
            for old_id, sealed_key in resealed.items()
        )
        finish_resealing_if_done(attempt)
    return stored


def finish_resealing_if_done(attempt, now=None):
    """Once nothing is left to reseal, draw the two checkers (step 4); with none, abandon."""
    new = new_key_of(attempt)
    if new is None or attempt.resealed_at is not None or vault.left_to_reseal(
        attempt.rotation.group_key, new, attempt.unopenable_items,
    ):
        return
    attempt.resealed_at = now or timezone.now()
    attempt.save(update_fields=['resealed_at'])
    for slot in (Slot.FIRST, Slot.SECOND):
        if not draw_slot(attempt, slot, now=now):
            return


# Checkers and confirmers (steps 4 to 6).

def draw_slot(attempt, slot, failed_items=(), also_excluding=(), now=None):
    """Draw a lawyer for the slot, among those eligible on the new key; with none, the attempt
    is abandoned. Returns whether one was drawn."""
    pool = eligible(new_key_of(attempt), attempt, also_excluding)
    if not pool:
        abandon(attempt, Because.NO_ONE_ELIGIBLE)
        return False
    RotationCheck.objects.create(
        attempt=attempt, slot=slot, lawyer_profile=draw(pool), drawn_at=now or timezone.now(),
        failed_items=list(failed_items),
    )
    return True


def checks_of(profile):
    """The unreplaced check `profile` holds in the running attempt, waiting for its result."""
    attempt = running_attempt()
    check = attempt and attempt.checks.filter(
        lawyer_profile=profile, replaced_at__isnull=True, result=Result.NONE,
    ).first()
    if check is None or profile.status != LawyerProfile.Status.LAWYER:
        raise Refused
    return check


def to_check(profile):
    """What a checker's browser checks (step 5): the data keys the rotator resealed, to open with
    the new key, and those the rotator could not open, which must not open with the old key
    either; a confirmer, only the items reported as failing. With their key check values."""
    check = checks_of(profile)
    attempt = check.attempt
    resealed = vault.resealed_by(new_key_of(attempt), attempt.rotator)
    unopenable = vault.sealed_by_id(attempt.rotation.group_key, attempt.unopenable_items)
    if check.slot == Slot.CONFIRMING:
        named = set(check.failed_items)
        resealed = [item for item in resealed if item['id'] in named]
        unopenable = [item for item in unopenable if item['id'] in named]
    return check, resealed, unopenable


def report(profile, passed, failed_items, new_public_key):
    """A checker's or confirmer's result, set once; then the attempt goes on (steps 6 to 9). It
    names the new key it checked: a result from a page shown during an earlier attempt, to the
    same lawyer drawn again, is refused rather than taken as this attempt's."""
    with transaction.atomic():
        grants.lock()
        profile = LawyerProfile.objects.get(pk=profile.pk)
        check = checks_of(profile)
        attempt = check.attempt
        if bytes(new_key_of(attempt).public_key) != new_public_key:
            raise Refused
        if passed:
            failed_items = []
        else:
            # Only items that were checked; none, when it is the checker's own grant on the new
            # key that did not open.
            known = {item['id'] for item in vault.resealed_by(new_key_of(attempt), attempt.rotator)}
            failed_items = sorted(set(failed_items) & (known | set(attempt.unopenable_items)))
        check.result = Result.PASSED if passed else Result.FAILED
        check.failed_items = failed_items if check.slot != Slot.CONFIRMING else check.failed_items
        check.save(update_fields=['result', 'failed_items'])
        # Who passed and who found a failure, kept in the append-only log.
        record(Action.ROTATION_CHECK_PASSED if passed else Action.ROTATION_CHECK_FAILED, profile, rotation_attempt=attempt)
        decide(attempt)


def decide(attempt):
    """What the attempt's checks call for, now: an end, a rejection, a confirmer, or waiting."""
    checks = {check.slot: check for check in attempt.checks.filter(replaced_at__isnull=True)}
    first, second, confirming = checks.get(Slot.FIRST), checks.get(Slot.SECOND), checks.get(Slot.CONFIRMING)
    results = [check.result for check in (first, second) if check]
    if results == [Result.PASSED, Result.PASSED]:
        end(attempt)
    elif results == [Result.FAILED, Result.FAILED]:
        # Two lawyers drawn independently agree: a rejection loses nothing.
        reject(attempt)
    elif Result.FAILED in results:
        failed = first if first.result == Result.FAILED else second
        if confirming is None:
            if not draw_slot(attempt, Slot.CONFIRMING, failed_items=failed.failed_items):
                # No one else could check it: alerted naming the checker whose failure stands alone.
                raise_alert(Alert.Kind.ROTATION_NO_ONE_ELIGIBLE, failed.lawyer_profile.user)
        elif confirming.result == Result.FAILED:
            reject(attempt)
        elif confirming.result == Result.PASSED:
            # One lawyer's word against another's.
            for lawyer in (failed.lawyer_profile, confirming.lawyer_profile):
                raise_alert(Alert.Kind.ROTATION_DISPUTED, lawyer.user)
            abandon(attempt, Because.DISPUTED)


# How an attempt ends (steps 7 to 9).

def end(attempt):
    """Two passed checks: the old key opens nothing in the database any more."""
    old, new = attempt.rotation.group_key, new_key_of(attempt)
    if vault.left_to_reseal(old, new, attempt.unopenable_items):
        # Never: checkers are drawn only once nothing is left. Deleting now would lose an item.
        raise Refused
    if attempt.unopenable_items:
        # No lawyer could open them: they lose their copy for the group key, which loses nothing
        # (a text keeps its code holder's). The admin is told which clips, by id.
        logger.warning('A rotation ended without %s data keys no one could open, of clips %s',
                       len(attempt.unopenable_items), ', '.join(vault.clips_of(attempt.unopenable_items)))
        raise_alert(Alert.Kind.ROTATION_UNOPENABLE_ITEMS)
    vault.forget_sealed_keys(old)
    KeyGrant.objects.filter(group_key=old).delete()
    old.status = Status.RETIRED
    old.save(update_fields=['status'])
    new.status = Status.CURRENT
    new.save(update_fields=['status'])
    _close(attempt, RotationAttempt.Outcome.ENDED)
    attempt.rotation.ended_at = attempt.ended_at
    attempt.rotation.save(update_fields=['ended_at'])
    record(Action.ROTATION_END, attempt.rotator, rotation_attempt=attempt)


def reject(attempt):
    _undo(attempt, Status.REJECTED)
    _close(attempt, RotationAttempt.Outcome.REJECTED)
    record(Action.ROTATION_REJECTION, attempt.rotator, rotation_attempt=attempt)
    raise_alert(Alert.Kind.ROTATION_REJECTED, attempt.rotator.user)
    start(attempt.rotation)


def abandon(attempt, because):
    """Give the attempt up: its new key, if made, loses its grants and sealed keys, and the old
    key waits for a new attempt; the admin is alerted, unless the archive started over."""
    _undo(attempt, Status.ABANDONED)
    _close(attempt, RotationAttempt.Outcome.ABANDONED, because)
    record(Action.ROTATION_ABANDON, attempt.rotator, rotation_attempt=attempt)
    if because == Because.ROTATOR_TIME:
        raise_alert(Alert.Kind.ROTATOR_TIME, attempt.rotator.user)
    elif because == Because.NO_ONE_ELIGIBLE:
        raise_alert(Alert.Kind.ROTATION_NO_ONE_ELIGIBLE)
    elif because in (Because.NEW_REQUEST, Because.ROTATOR_SUSPENDED, Because.ROTATOR_KEY_RESET):
        raise_alert(Alert.Kind.ROTATION_ABANDONED, attempt.rotator.user)
    if because not in (Because.STARTED_OVER, Because.NEW_REQUEST):
        start(attempt.rotation)


def _undo(attempt, status):
    new = new_key_of(attempt)
    if new is not None:
        vault.forget_sealed_keys(new)
        new.grants.all().delete()
        new.status = status
        new.save(update_fields=['status'])
    old = attempt.rotation.group_key
    if old.status == Status.RETIRING:
        old.status = Status.ROTATION_REQUESTED
        old.save(update_fields=['status'])


def _close(attempt, outcome, because=''):
    attempt.ended_at = timezone.now()
    attempt.outcome = outcome
    attempt.abandoned_because = because
    attempt.save(update_fields=['ended_at', 'outcome', 'abandoned_because'])


def replace(attempt, check, also_excluding=(), now=None):
    """Draw the check's slot again. A confirmer goes with the failure they were confirming: once
    the checker who reported it is replaced, their failure no longer stands, and the new
    checker's result decides afresh. Returns whether the attempt goes on."""
    now = now or timezone.now()
    check.replaced_at = now
    check.save(update_fields=['replaced_at'])
    if check.slot != Slot.CONFIRMING and check.result == Result.FAILED:
        attempt.checks.filter(slot=Slot.CONFIRMING, replaced_at__isnull=True).update(replaced_at=now)
    failed_items = check.failed_items if check.slot == Slot.CONFIRMING else ()
    return draw_slot(attempt, check.slot, failed_items, also_excluding=also_excluding, now=now)


# Suspension (step 4), key resets (§5.6) and starting over (§5.7).

def suspended(profile):
    """A lawyer just suspended loses every role they hold in the running attempt: a checker's or
    confirmer's slot is drawn again, even if they passed; a rotator still resealing abandons it.
    Call under the lock."""
    _drop_roles(profile, Because.ROTATOR_SUSPENDED)


def key_reset(profile):
    """A lawyer who just reset their key holds the group key no more: they lose their roles as
    a suspended lawyer does, rather than holding up the attempt until their time runs out.
    Call under the lock."""
    _drop_roles(profile, Because.ROTATOR_KEY_RESET)


def _drop_roles(profile, because):
    attempt = running_attempt()
    if attempt is None:
        return
    if attempt.rotator_id == profile.pk and attempt.resealed_at is None:
        abandon(attempt, because)
        return
    for check in attempt.checks.filter(lawyer_profile=profile, replaced_at__isnull=True):
        if not replace(attempt, check):
            return


def started_over():
    """The archive starts over with a new group key (§5.7): any running rotation and attempt end.
    Call under the lock, before the earlier keys are retired."""
    attempt = running_attempt()
    if attempt is not None:
        abandon(attempt, Because.STARTED_OVER)
    rotation = running()
    if rotation is not None:
        rotation.ended_at = timezone.now()
        rotation.save(update_fields=['ended_at'])


# Time (steps 2 to 6): run by the background worker (keys.tasks).

def tick(now=None):
    """Start what can start, and give up or draw again what has waited too long."""
    now = now or timezone.now()
    with transaction.atomic():
        grants.lock()
        rotation = running()
        if rotation is None:
            return
        attempt = running_attempt()
        if attempt is None:
            start(rotation, now=now)
            return
        if attempt.resealed_at is None:
            finish_resealing_if_done(attempt, now=now)
            attempt.refresh_from_db()
            if attempt.resealed_at is None and attempt.ended_at is None and now - attempt.drawn_at >= role_days():
                abandon(attempt, Because.ROTATOR_TIME)
            return
        for check in attempt.checks.filter(replaced_at__isnull=True, result=Result.NONE, drawn_at__lte=now - role_days()):
            raise_alert(Alert.Kind.ROTATION_ROLE_REDRAWN, check.lawyer_profile.user)
            if not replace(attempt, check, also_excluding={check.lawyer_profile_id}, now=now):
                return
