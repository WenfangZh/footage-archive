"""The audit log: every lawyer action, and the admin's actions on accounts (SPEC.md §7).

It is append-only: entries are only ever added, meant to be through `record`. Every way
the models offer to change or delete an entry refuses, including bulk QuerySet methods,
the manager Django uses internally and the update that save_base() and fixtures would
run. What bypasses the models is outside this guard: raw SQL and migrations. Entries hold keys and identifiers, never text typed by
anyone (CLAUDE.md).

M3 records the admin's actions on applicants' and lawyers' accounts. Lawyers' own actions
(M4, M6) bring the case, clip and count that SPEC.md §7 lists.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from accounts.models import LawyerProfile


class Action(models.TextChoices):
    INVITATION = 'invitation', _('Invitation')
    DECLINING = 'declining', _('Declining')
    ADMISSION = 'admission', _('Admission')
    SUSPENSION = 'suspension', _('Suspension')
    REINSTATEMENT = 'reinstatement', _('Reinstatement')
    REVOCATION = 'revocation', _('Revocation')


# Actions the admin takes; every other action is a lawyer's own.
ADMIN_ACTIONS = [
    Action.INVITATION, Action.DECLINING, Action.ADMISSION, Action.SUSPENSION,
    Action.REINSTATEMENT, Action.REVOCATION,
]


class AppendOnly(Exception):
    """Something tried to change or delete an audit entry."""


class AuditEntryQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnly('Audit entries cannot be changed.')

    def bulk_update(self, objs, fields, batch_size=None):
        raise AppendOnly('Audit entries cannot be changed.')

    def bulk_create(self, objs, *args, **kwargs):
        # It skips save(), and with update_conflicts it overwrites existing entries.
        raise AppendOnly('Audit entries are added one at a time, through record().')

    def delete(self):
        raise AppendOnly('Audit entries cannot be deleted.')


class AuditEntry(models.Model):
    """One action in the audit log: what was done, about whom, by the admin or not, and when."""

    action = models.CharField(max_length=30, choices=Action.choices)
    # The applicant or lawyer the entry is about: the lawyer who acted, or the person
    # whose account the admin changed.
    lawyer_profile = models.ForeignKey(LawyerProfile, on_delete=models.PROTECT, related_name='+')
    # The admin's account, on the admin's actions; empty on a lawyer's own.
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+',
    )
    # The time of writing, never one passed in. Precise: alerts look for many actions in a
    # short time (SPEC.md §7).
    at = models.DateTimeField(auto_now_add=True)

    objects = AuditEntryQuerySet.as_manager()

    class Meta:
        # Django's internal updates and deletions go through this manager too.
        base_manager_name = 'objects'
        constraints = [
            models.CheckConstraint(condition=Q(action__in=Action.values), name='known_action'),
            models.CheckConstraint(
                condition=Q(action__in=ADMIN_ACTIONS, admin__isnull=False)
                | (~Q(action__in=ADMIN_ACTIONS) & Q(admin__isnull=True)),
                name='admin_on_admin_actions_only',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnly('Audit entries cannot be changed.')
        if self.action not in Action.values:
            raise ValueError(f'{self.action!r} is not an audit log action.')
        # The stored account, not the copy passed in, which may have been changed unsaved.
        if self.admin_id is not None and not get_user_model().objects.filter(
            pk=self.admin_id, is_staff=True,
        ).exists():
            raise ValueError("Only the admin's account can be the admin on an audit entry.")
        # Always a new row: an entry given an existing id fails instead of replacing it.
        kwargs['force_insert'] = True
        super().save(*args, **kwargs)

    def _do_update(self, *args, **kwargs):
        # Where every UPDATE of an instance ends, including save_base() and loading fixtures.
        raise AppendOnly('Audit entries cannot be changed.')

    def delete(self, *args, **kwargs):
        raise AppendOnly('Audit entries cannot be deleted.')


def record(action, lawyer_profile, admin=None):
    """Add an entry to the audit log: the way every entry is meant to be written."""
    return AuditEntry.objects.create(action=action, lawyer_profile=lawyer_profile, admin=admin)
