"""Recovery (docs/m5-encryption-design.md §5.6): a forgotten key passphrase replaced with the
recovery key, a key reset when both are lost, a report of a passphrase or recovery key that
may have been seen, and the admin's confirmation of the new key a lawyer sets up after one.

A new passphrase changes only the encrypted copy it opens: the key, its safety number and its
grants stay. A key reset retires the key and deletes its grants; what was sealed only to it
(case labels) is lost. The admin alone can neither reset a key nor give the group key.
"""

from django.db import transaction
from django.utils import timezone

from accounts.models import LawyerProfile
from audit.models import Action, record

from . import grants, rotation
from .models import MEMLIMIT, OPSLIMIT, KeyGrant, LawyerKey


class Refused(Exception):
    """The key changed meanwhile, or the person may not do this."""


def replace_passphrase(profile, public_key, passphrase_salt, private_key_under_passphrase):
    """Store the private key encrypted under a new key passphrase, which the browser set after
    opening the key with the recovery key. Only for the current key with this public half, so
    that a key replaced meanwhile in another tab is never given another key's copy."""
    with transaction.atomic():
        key = LawyerKey.objects.current().select_for_update().filter(lawyer_profile=profile, public_key=public_key).first()
        if key is None:
            raise Refused
        key.passphrase_salt, key.private_key_under_passphrase = passphrase_salt, private_key_under_passphrase
        key.opslimit, key.memlimit = OPSLIMIT, MEMLIMIT
        # The browser opened the key and checked it, as an unlock does (design §5.5's draws).
        key.last_unlocked_on = timezone.localdate()
        key.save(update_fields=['passphrase_salt', 'private_key_under_passphrase', 'opslimit', 'memlimit', 'last_unlocked_on'])
        record(Action.PASSPHRASE_REPLACEMENT, profile, lawyer_key=key)


def reset(profile, key_id):
    """A lawyer who lost both their key passphrase and their recovery key: their key `key_id` is
    retired and its grants deleted, and a rotation is requested, as for a revocation: lost paper
    may be found, and with any older copy of the database it opens the key, its grant and the
    group key (design §5.6). A running attempt is abandoned, since they may hold its new key or
    a role in it. They set up a new key, which the admin confirms and a lawyer grants the group
    key to, as for a newcomer. Refused if `key_id` is no longer their current key: a page shown
    before a reset in another tab must not reset the key set up since."""
    with transaction.atomic():
        grants.lock()
        profile = LawyerProfile.objects.select_for_update().get(pk=profile.pk)
        key = grants.current_key(profile)
        if profile.status != LawyerProfile.Status.LAWYER or key is None or key.pk != key_id:
            raise Refused
        KeyGrant.objects.filter(lawyer_key=key).delete()
        key.retire()
        record(Action.KEY_RESET, profile, lawyer_key=key)
        rotation.request(profile)


class LastHolder(Refused):
    """No other admitted, unsuspended lawyer holds the group key to give it to the new key."""


class LabelsChanged(Refused):
    """The labels sealed to the old key are not the ones resealed: a case was made meanwhile."""


def report_exposure(profile, old_public_key, new_key, resealed, unreadable):
    """A lawyer whose key passphrase or recovery key may have been seen, and who still holds their
    key: their browser made a new key, `new_key` (as setup takes it), and resealed to it the data
    key of each label sealed to the old one, `resealed` by case id; `unreadable` names the labels
    it could not open, which stay sealed to the old key and are lost. The old key is retired and
    its grants deleted, and a rotation is requested. Refused unless `resealed` and `unreadable`
    cover exactly the labels sealed to the old key, under the lock case creation also takes, and
    while no other lawyer could give the group key to the new key (design §5.6)."""
    from cases import labels  # Here: cases.labels imports keys.views, which imports this module.
    with transaction.atomic():
        grants.lock()
        profile = LawyerProfile.objects.select_for_update().get(pk=profile.pk)
        old = grants.current_key(profile)
        # Only with the group key held through a confirmed key: so a report cannot be made again
        # before the new key is confirmed and granted, to keep abandoning rotations.
        if old is None or bytes(old.public_key) != old_public_key or not grants.reads_vault(profile):
            raise Refused
        if not grants.another_can_grant(profile):
            raise LastHolder
        sealed = {label['id'] for label in labels.sealed_to(old)}
        if set(resealed) & set(unreadable) or set(resealed) | set(unreadable) != sealed:
            raise LabelsChanged
        KeyGrant.objects.filter(lawyer_key=old).delete()
        old.retire()
        new = LawyerKey.objects.create(lawyer_profile=profile, opslimit=OPSLIMIT, memlimit=MEMLIMIT, **new_key)
        labels.reseal(old, new, resealed)
        record(Action.KEY_EXPOSURE_REPORT, profile, lawyer_key=old)
        rotation.request(profile)
    return new


def unconfirmed_key(profile_id):
    """An admitted lawyer's current key the admin has not confirmed yet: one set up after a key
    reset, or made at an exposure report (design §5.6). None otherwise."""
    return LawyerKey.objects.current().filter(
        lawyer_profile_id=profile_id, lawyer_profile__status=LawyerProfile.Status.LAWYER, admin_confirmed_at__isnull=True,
    ).select_related('lawyer_profile__user').first()


def confirm(admin, profile_id, key_id):
    """The admin compared the safety number of the key `key_id` with the lawyer (design §5.2).
    Refused if it is no longer their unconfirmed current key: one made since was not the one
    compared."""
    with transaction.atomic():
        key = unconfirmed_key(profile_id)
        if key is None or key.pk != key_id:
            raise Refused
        key = LawyerKey.objects.select_for_update().get(pk=key.pk)
        if key.retired_at is not None or key.admin_confirmed_at is not None:
            raise Refused
        key.admin_confirmed_at = timezone.now()
        key.save(update_fields=['admin_confirmed_at'])
        record(Action.KEY_CONFIRMATION, key.lawyer_profile, admin=admin, lawyer_key=key)
