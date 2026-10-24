"""Who holds the group key, and handing it over (docs/m5-encryption-design.md §3.3, §5.3, §5.7).

The browsers do the cryptography: one makes the group key, and each grant is sealed by a
lawyer's browser to a colleague's personal key. The server stores what they send, and
decides who may read the vault and when uploads are open.

Every change here takes the group key lock first: a select_for_update on the group keys in
use, which rotations take too (design §5.5). SQLite runs each transaction as IMMEDIATE, so
writers queue even when no group key exists yet to lock.
"""

from django.db import transaction
from django.utils import timezone

from accounts.models import LawyerProfile
from alerts.checks import raise_alert
from alerts.models import Alert
from audit.models import Action, record

from .models import IN_USE, GroupKey, KeyGrant, LawyerKey

Status = LawyerProfile.Status


class Refused(Exception):
    """The request does not match the archive's keys as they are now: the browser fetches
    them again, or the page says why."""


def in_use():
    return GroupKey.objects.filter(status__in=IN_USE)


def lock():
    """The group key lock (design §5.5), held until the transaction ends."""
    return list(in_use().select_for_update().order_by('pk'))


def current_key(profile):
    return LawyerKey.objects.current().filter(lawyer_profile=profile).first()


def held():
    """Grants held by lawyers who are not revoked (design §5.7): a suspended lawyer may be
    reinstated, and still holds theirs."""
    return KeyGrant.objects.exclude(lawyer_key__lawyer_profile__status=Status.REVOKED)


def reads_vault(profile):
    """Whether the vault serves this person anything (design §3.3): an admitted, unsuspended
    lawyer whose current personal key holds a grant on a group key in use."""
    return profile.status == Status.LAWYER and KeyGrant.objects.filter(
        lawyer_key__lawyer_profile=profile, lawyer_key__retired_at__isnull=True, group_key__status__in=IN_USE,
    ).exists()


def uploads_open():
    """Whether a lawyer who is not revoked holds a grant on the newest group key in use, so
    that what is uploaded can be read (design §5.7, §10)."""
    newest = in_use().order_by('-made_at', '-pk').first()
    return newest is not None and held().filter(group_key=newest).exists()


def may_start(profile):
    """Whether this lawyer's browser should make a new group key: no one who is not revoked
    holds a grant on any group key, and they are a lawyer with a confirmed key (design §5.3, §5.7)."""
    key = current_key(profile)
    return (
        profile.status == Status.LAWYER and key is not None and key.admin_confirmed_at is not None
        and not held().exists()
    )


def start(profile, public_key, sealed_private_key):
    """Store the group key a lawyer's browser made, and its grant to them; ends every earlier
    group key still in use, which no one left can open (design §5.7)."""
    with transaction.atomic():
        earlier = lock()
        profile = LawyerProfile.objects.select_for_update().get(pk=profile.pk)
        if not may_start(profile):
            raise Refused
        for key in earlier:
            # A new key whose rotation could not end, or the key it would have replaced.
            key.status = GroupKey.Status.ABANDONED if key.status == GroupKey.Status.CHECKING else GroupKey.Status.RETIRED
            key.save(update_fields=['status'])
        group_key = GroupKey.objects.create(public_key=public_key)
        KeyGrant.objects.create(
            group_key=group_key, lawyer_key=current_key(profile), sealed_private_key=sealed_private_key,
            granted_by=profile, checked_at=timezone.now(),
        )
        started_over = GroupKey.objects.exclude(pk=group_key.pk).exists()
        record(Action.STARTING_OVER if started_over else Action.GROUP_KEY_CREATION, profile)
    return group_key


def missing(lawyer_key):
    """The group keys in use that this personal key holds no grant on."""
    return in_use().exclude(grants__lawyer_key=lawyer_key).order_by('pk')


def waiting():
    """Admitted, unsuspended lawyers whose confirmed key misses a grant on a group key in use."""
    keys = LawyerKey.objects.current().filter(
        admin_confirmed_at__isnull=False, lawyer_profile__status=Status.LAWYER,
    ).select_related('lawyer_profile__user').order_by('made_at')
    return [key for key in keys if missing(key).exists()]


def grant(granter, recipient_key_id, sealed):
    """Store the grants a lawyer's browser sealed to a colleague's key, one per group key the
    colleague misses (design §5.3). `sealed` maps group key ids to sealed private keys."""
    with transaction.atomic():
        lock()
        recipient_key = LawyerKey.objects.current().filter(
            pk=recipient_key_id, admin_confirmed_at__isnull=False, lawyer_profile__status=Status.LAWYER,
        ).exclude(lawyer_profile=granter).select_related('lawyer_profile').first()
        if recipient_key is None:
            raise Refused
        group_keys = list(missing(recipient_key))
        granter_key = current_key(granter)
        # The granter seals only what they hold: every group key they hand over.
        holds_each = granter_key is not None and all(
            KeyGrant.objects.filter(group_key=key, lawyer_key=granter_key).exists() for key in group_keys
        )
        if not group_keys or {key.pk for key in group_keys} != set(sealed) or not holds_each:
            raise Refused
        for key in group_keys:
            KeyGrant.objects.create(
                group_key=key, lawyer_key=recipient_key, sealed_private_key=sealed[key.pk], granted_by=granter,
            )
        record(Action.KEY_GRANT, granter, lawyer_key=recipient_key)
    return recipient_key


def report_check(profile, grant_id, passed):
    """The recipient's browser opened a grant to their current key (design §4). A grant that
    failed is deleted, so that they wait for a new one, and the admin is alerted."""
    with transaction.atomic():
        lock()
        held_grant = KeyGrant.objects.filter(
            pk=grant_id, lawyer_key__lawyer_profile=profile, lawyer_key__retired_at__isnull=True,
            group_key__status__in=IN_USE, checked_at__isnull=True,
        ).first()
        if held_grant is None:
            raise Refused
        if passed:
            held_grant.checked_at = timezone.now()
            held_grant.save(update_fields=['checked_at'])
        else:
            held_grant.delete()
            raise_alert(Alert.Kind.KEY_GRANT_FAILED, profile.user)
