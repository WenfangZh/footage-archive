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

    kind = models.CharField(max_length=30, choices=Kind.choices)
    account = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+',
    )
    raised_at = models.DateTimeField(default=timezone.now)
    seen_at = models.DateTimeField(null=True, blank=True)
