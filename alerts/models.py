"""Alerts to the admin (SPEC.md §5): shown on the admin's pages, not sent by email for now.

M3 has two kinds: a backed-up ingest queue (SPEC.md §9), and many failed logins for one
account (SPEC.md §10). M4 adds unusual lawyer activity. An alert holds a kind, the
account it concerns if any, and when it was raised and seen: never text from anyone.
Raising and checking them is in alerts.checks.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _



class Alert(models.Model):
    class Kind(models.TextChoices):
        QUEUE_BACKED_UP = 'queue_backed_up', _('The ingest queue is backed up')
        FAILED_LOGINS = 'failed_logins', _('Many failed logins for one account')
        MANY_DOWNLOADS = 'many_downloads', _('Many downloads by one lawyer in a short time')
        MANY_CASES = 'many_cases', _('Many cases created by one lawyer in a short time')

    kind = models.CharField(max_length=30, choices=Kind.choices)
    account = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+',
    )
    raised_at = models.DateTimeField(default=timezone.now)
    seen_at = models.DateTimeField(null=True, blank=True)


class AlertLimitRaise(models.Model):
    """The admin raising one lawyer's limit for an activity alert, for a few days (SPEC.md §7).

    Such as for a lawyer working a large case. It is written once and never changed: a new
    raise replaces it while both last, and the highest limit still in force applies.
    """

    class Activity(models.TextChoices):
        DOWNLOADS = 'downloads', _('Downloads')
        CASES = 'cases', _('Cases created')

    lawyer_profile = models.ForeignKey('accounts.LawyerProfile', on_delete=models.PROTECT, related_name='+')
    activity = models.CharField(max_length=20, choices=Activity.choices)
    limit = models.PositiveIntegerField()
    raised_at = models.DateTimeField(default=timezone.now)
    until = models.DateTimeField()

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(activity__in=['downloads', 'cases']), name='known_activity'),
            models.CheckConstraint(condition=models.Q(until__gt=models.F('raised_at')), name='ends_after_it_starts'),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError('A raise of an alert limit is never changed.')
        super().save(*args, **kwargs)
