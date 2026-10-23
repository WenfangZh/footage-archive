"""Lawyers' personal keys (docs/m5-encryption-design.md §3, §5; ADR 0007).

A personal key is made in the lawyer's browser. The server keeps its public half, and its
private half only encrypted twice: under the lawyer's key passphrase and under their
recovery key, neither of which it ever sees. A lawyer has at most one current key; an
older one is retired, which erases both encrypted copies of its private half, while its
public half stays for the audit log's sake.
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
