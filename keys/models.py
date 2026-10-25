"""Lawyers' personal keys, the group key and key grants (docs/m5-encryption-design.md §3, §5; ADR 0007).

A personal key is made in the lawyer's browser. The server keeps its public half, and its
private half only encrypted twice: under the lawyer's key passphrase and under their
recovery key, neither of which it ever sees. A lawyer has at most one current key; an
older one is retired, which erases both encrypted copies of its private half, while its
public half stays for the audit log's sake.

The group key is one key pair shared by all lawyers, made in a lawyer's browser too. The
server keeps its public half, and its private half only sealed to each lawyer's personal
key: one key grant each.
"""

from django.db import models
from django.db.models import Q
from django.utils import timezone
from nacl.encoding import RawEncoder
from nacl.hash import blake2b

from accounts.models import LawyerProfile

PUBLIC_KEY_BYTES = 32
SALT_BYTES = 16
# XChaCha20-Poly1305 as the browser writes it: a 24-byte nonce, then the 32-byte private
# key and the 16-byte tag.
ENCRYPTED_PRIVATE_KEY_BYTES = 24 + 32 + 16
# libsodium's "interactive" Argon2id limits (design §4): with a generated passphrase, more
# slowness adds little, and phones have little memory. Stored with each key, so that they
# can change later without locking anyone out.
OPSLIMIT = 2
MEMLIMIT = 64 * 1024 * 1024

SAFETY_NUMBER_LABEL = b'fa-safetynum-v1'


def safety_number(public_key):
    """Six groups of five digits that two people read to each other (design §3.4)."""
    digest = blake2b(SAFETY_NUMBER_LABEL + bytes(public_key), digest_size=32, encoder=RawEncoder)
    return ' '.join(f'{int.from_bytes(digest[i:i + 5], "big") % 100_000:05d}' for i in range(0, 30, 5))


class LawyerKeyQuerySet(models.QuerySet):
    def current(self):
        return self.filter(retired_at__isnull=True)


class LawyerKey(models.Model):
    lawyer_profile = models.ForeignKey(LawyerProfile, on_delete=models.PROTECT, related_name='keys')
    public_key = models.BinaryField(max_length=PUBLIC_KEY_BYTES)
    # Argon2id turns the key passphrase into the key that encrypts the private half.
    passphrase_salt = models.BinaryField(max_length=SALT_BYTES)
    opslimit = models.PositiveIntegerField(default=OPSLIMIT)
    memlimit = models.PositiveBigIntegerField(default=MEMLIMIT)
    # Empty once the key is retired.
    private_key_under_passphrase = models.BinaryField(max_length=ENCRYPTED_PRIVATE_KEY_BYTES)
    private_key_under_recovery_key = models.BinaryField(max_length=ENCRYPTED_PRIVATE_KEY_BYTES)
    made_at = models.DateTimeField(default=timezone.now)
    # When the admin compared its safety number with the applicant (design §5.2). It belongs
    # to the key, so a new key starts unconfirmed.
    admin_confirmed_at = models.DateTimeField(null=True, blank=True)
    # The day only: rotations draw among lawyers who unlocked recently (design §5.5).
    last_unlocked_on = models.DateField(null=True, blank=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    objects = LawyerKeyQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['lawyer_profile'], condition=Q(retired_at__isnull=True), name='one_current_key_per_lawyer',
            ),
        ]

    def safety_number(self):
        return safety_number(self.public_key)

    def retire(self):
        self.private_key_under_passphrase = b''
        self.private_key_under_recovery_key = b''
        self.retired_at = timezone.now()
        self.save(update_fields=['private_key_under_passphrase', 'private_key_under_recovery_key', 'retired_at'])


# crypto_box_seal: a 32-byte ephemeral public key, then the 32-byte private key and a 16-byte tag.
SEALED_GROUP_KEY_BYTES = 32 + 32 + 16


class GroupKey(models.Model):
    class Status(models.TextChoices):
        CURRENT = 'current'
        # Current, and a rotation is due (design §5.5).
        ROTATION_REQUESTED = 'rotation_requested'
        # The new key of a rotation, waiting for its checks.
        CHECKING = 'checking'
        # The old key during a rotation.
        RETIRING = 'retiring'
        RETIRED = 'retired'
        # A new key whose checks failed.
        REJECTED = 'rejected'
        # A new key whose rotation was given up.
        ABANDONED = 'abandoned'

    public_key = models.BinaryField(max_length=PUBLIC_KEY_BYTES)
    made_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CURRENT)
    # The rotation attempt that made it; empty for the first key and after starting over.
    attempt = models.OneToOneField(
        'RotationAttempt', null=True, blank=True, on_delete=models.PROTECT, related_name='new_key',
    )

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(status__in=[
                'current', 'rotation_requested', 'checking', 'retiring', 'retired', 'rejected', 'abandoned',
            ]), name='known_group_key_status'),
            # The key in use before any rotation running: at most one.
            models.UniqueConstraint(
                models.Value(True), condition=Q(status__in=['current', 'rotation_requested', 'retiring']),
                name='one_established_group_key',
            ),
            # The new key of the rotation running: at most one.
            models.UniqueConstraint(models.Value(True), condition=Q(status='checking'), name='one_checking_group_key'),
            # A key a rotation made, and only such a key, is or was checked.
            models.CheckConstraint(
                condition=~Q(status__in=['checking', 'rejected', 'abandoned']) | Q(attempt__isnull=False),
                name='rotation_keys_name_their_attempt',
            ),
        ]


# Keys that ingest seals to and lawyers read with (design §3.3, §5.5).
IN_USE = [GroupKey.Status.CURRENT, GroupKey.Status.ROTATION_REQUESTED, GroupKey.Status.CHECKING, GroupKey.Status.RETIRING]


class KeyGrant(models.Model):
    """The group private key, sealed to one lawyer's personal key (design §5.3)."""

    group_key = models.ForeignKey(GroupKey, on_delete=models.PROTECT, related_name='grants')
    lawyer_key = models.ForeignKey(LawyerKey, on_delete=models.PROTECT, related_name='grants')
    sealed_private_key = models.BinaryField(max_length=SEALED_GROUP_KEY_BYTES)
    # The lawyer whose browser sealed it: the recipient themselves for the first group key.
    granted_by = models.ForeignKey(LawyerProfile, on_delete=models.PROTECT, related_name='+')
    granted_at = models.DateTimeField(default=timezone.now)
    # When the recipient's browser opened it and found the group key's private half (design §4).
    checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['group_key', 'lawyer_key'], name='one_grant_per_group_key_and_lawyer_key'),
        ]


class Rotation(models.Model):
    """Replacing a group key, after a revocation or a reported exposure (design §5.5). It runs
    until an attempt ends it, or the archive starts over (§5.7); attempts that fail are retried."""

    group_key = models.ForeignKey(GroupKey, on_delete=models.PROTECT, related_name='rotations')
    requested_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(models.Value(True), condition=Q(ended_at__isnull=True), name='one_rotation_running'),
        ]


class RotationAttempt(models.Model):
    """One try at a rotation: a rotator drawn at random makes the new key and reseals; two
    checkers, and a confirmer for a reported failure, check it (design §5.5)."""

    class Outcome(models.TextChoices):
        ENDED = 'ended'
        REJECTED = 'rejected'
        ABANDONED = 'abandoned'

    class AbandonedBecause(models.TextChoices):
        ROTATOR_TIME = 'rotator_time'
        DISPUTED = 'disputed'
        NO_ONE_ELIGIBLE = 'no_one_eligible'
        NEW_REQUEST = 'new_request'
        STARTED_OVER = 'started_over'
        # The rotator was suspended before resealing was done (design §5.5, step 4).
        ROTATOR_SUSPENDED = 'rotator_suspended'

    rotation = models.ForeignKey(Rotation, on_delete=models.PROTECT, related_name='attempts')
    rotator = models.ForeignKey(LawyerProfile, on_delete=models.PROTECT, related_name='+')
    drawn_at = models.DateTimeField(default=timezone.now)
    # When nothing was left to reseal, and the checkers were drawn.
    resealed_at = models.DateTimeField(null=True, blank=True)
    # Data keys sealed to the old key that the rotator's browser could not open, as their ids
    # (clips.models.SealedKey): resealing goes on without them, and the checkers check the claim.
    unopenable_items = models.JSONField(default=list, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=20, choices=Outcome.choices, blank=True)
    abandoned_because = models.CharField(max_length=20, choices=AbandonedBecause.choices, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['rotation'], condition=Q(ended_at__isnull=True), name='one_attempt_running_per_rotation',
            ),
            # Ended with an outcome, and abandoned with why; running with neither.
            models.CheckConstraint(
                condition=Q(ended_at__isnull=True, outcome='', abandoned_because='')
                | Q(ended_at__isnull=False, outcome__in=['ended', 'rejected'], abandoned_because='')
                | (Q(ended_at__isnull=False, outcome='abandoned') & ~Q(abandoned_because='')),
                name='attempt_outcome_when_ended',
            ),
        ]


class RotationCheck(models.Model):
    """One role of checking an attempt's new key (design §5.5, steps 4 to 6)."""

    class Slot(models.TextChoices):
        FIRST = 'first'
        SECOND = 'second'
        # Draws one lawyer to check items a checker reported as failing.
        CONFIRMING = 'confirming'

    class Result(models.TextChoices):
        NONE = ''
        PASSED = 'passed'
        FAILED = 'failed'

    attempt = models.ForeignKey(RotationAttempt, on_delete=models.PROTECT, related_name='checks')
    slot = models.CharField(max_length=20, choices=Slot.choices)
    lawyer_profile = models.ForeignKey(LawyerProfile, on_delete=models.PROTECT, related_name='+')
    drawn_at = models.DateTimeField(default=timezone.now)
    result = models.CharField(max_length=10, choices=Result.choices, blank=True)
    # The items that failed, as their sealed keys' ids (clips.models.SealedKey); for a
    # confirmer, those a checker named.
    failed_items = models.JSONField(default=list, blank=True)
    # When the slot was drawn again: a result from a replaced holder is refused.
    replaced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['attempt', 'slot'], condition=Q(replaced_at__isnull=True), name='one_holder_per_slot',
            ),
            models.UniqueConstraint(
                fields=['attempt', 'lawyer_profile'], condition=Q(replaced_at__isnull=True), name='one_role_per_lawyer',
            ),
        ]
