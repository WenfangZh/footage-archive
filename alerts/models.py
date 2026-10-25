"""Alerts to the admin (SPEC.md §5): shown on the admin's pages, not sent by email for now.

M3 has two kinds: a backed-up ingest queue (SPEC.md §9), and many failed logins for one
account (SPEC.md §10). M4 adds unusual lawyer activity, M5 a key grant that its recipient's
browser could not open (docs/m5-encryption-design.md §4). An alert holds a kind, the
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
        KEY_GRANT_FAILED = 'key_grant_failed', _('A key grant did not open for the lawyer who received it')
        # Rotations of the group key (docs/m5-encryption-design.md §5.5): each names the lawyer concerned.
        ROTATION_NOT_STARTING = 'rotation_not_starting', _('A rotation of the group key has waited long for enough lawyers')
        ROTATOR_TIME = 'rotator_time', _('A rotator did not finish resealing in time')
        ROTATION_ROLE_REDRAWN = 'rotation_role_redrawn', _('A lawyer drawn to check a rotation did not, in time')
        ROTATION_NO_ONE_ELIGIBLE = 'rotation_no_one_eligible', _('No lawyer could be drawn for a rotation')
        ROTATION_DISPUTED = 'rotation_disputed', _('Two lawyers disagree about a rotation')
        ROTATION_REJECTED = 'rotation_rejected', _('A rotation of the group key failed its checks')
        ROTATION_ABANDONED = 'rotation_abandoned', _('A rotation attempt was given up: a new request, or its rotator suspended or their key reset')
        ROTATION_UNOPENABLE_ITEMS = 'rotation_unopenable_items', _('A rotation found items no lawyer could open: see the log for their clips')

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
