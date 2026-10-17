"""Cases (SPEC.md §7): a lawyer's search scope for one incident.

M3 has only what revocation needs: whose case it is, its case label, and whether it is
closed and why. M4 adds the time window and area, the size caps, creating cases, and
search. A case is opened only for a lawyer, and never deleted: a closed case stays as it
was, and in the audit log. Closing goes through `close`.
"""

from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import LawyerProfile


class CaseQuerySet(models.QuerySet):
    def open(self):
        return self.filter(closed_at__isnull=True)

    def close(self, because, now=None):
        """Close the open cases among these; closed ones keep their time and reason."""
        return super(CaseQuerySet, self.open()).update(closed_at=now or timezone.now(), closed_because=because)

    def update(self, **kwargs):
        raise ValueError('Cases change only by being closed, through close().')

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValueError('Cases change only by being closed, through close().')

    def delete(self):
        raise ValueError('Cases are never deleted.')


class Case(models.Model):
    class ClosedBecause(models.TextChoices):
        LAWYER = 'lawyer', _('Closed by its lawyer')
        REVOCATION = 'revocation', _("Closed when its lawyer's access was revoked")
        INACTIVITY = 'inactivity', _('Closed after a long time without use')

    lawyer_profile = models.ForeignKey(LawyerProfile, on_delete=models.PROTECT, related_name='cases')
    # Never a client's name (SPEC.md §7).
    label = models.CharField(max_length=100)
    opened_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_because = models.CharField(max_length=20, choices=ClosedBecause.choices, blank=True)

    objects = CaseQuerySet.as_manager()

    class Meta:
        base_manager_name = 'objects'
        constraints = [
            models.CheckConstraint(
                condition=Q(closed_because__in=['', 'lawyer', 'revocation', 'inactivity']), name='known_reason',
            ),
            models.CheckConstraint(
                condition=Q(closed_at__isnull=True, closed_because='')
                | (Q(closed_at__isnull=False) & ~Q(closed_because='')),
                name='closed_with_a_reason',
            ),
        ]

    def save(self, *args, **kwargs):
        if self._state.adding:
            if LawyerProfile.objects.filter(pk=self.lawyer_profile_id).values_list('status', flat=True).first() != (
                LawyerProfile.Status.LAWYER
            ):
                raise ValueError('Only a lawyer can have a new case.')
            # Always a new row: a case given an existing id fails instead of replacing it.
            kwargs['force_insert'] = True
        elif Case.objects.filter(pk=self.pk, closed_at__isnull=False).exists():
            raise ValueError('A closed case never changes again.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Cases are never deleted.')
