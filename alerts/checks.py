"""Raising alerts (SPEC.md §5, §9, §10): a backed-up ingest queue, many failed logins."""

import datetime

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from clips import rate_limits
from clips.models import Clip
from clips.time_blocks import four_hour_block

from .models import Alert


def raise_alert(kind, account=None, raised_at=None):
    """Raise an alert, unless one of this kind, for this account, is still unseen."""
    if not Alert.objects.filter(kind=kind, account=account, seen_at__isnull=True).exists():
        Alert.objects.create(kind=kind, account=account, raised_at=raised_at or timezone.now())


def check_ingest_queue(now=None):
    """Alert when too many clips wait for ingest, or one has waited too long (SPEC.md §9).

    How long a clip has waited is known only from the end of its 4-hour upload block,
    since no more precise upload time is stored: a clip still waiting QUEUE_ALERT_HOURS
    after its block ended has waited at least that long.
    """
    now = now or timezone.now()
    waiting = Clip.objects.filter(status=Clip.Status.PROCESSING)
    # A block ended QUEUE_ALERT_HOURS ago or more exactly when it started before the block
    # holding that moment: blocks last 3 or 5 hours on the days the clocks change.
    cutoff = four_hour_block(now - datetime.timedelta(hours=settings.QUEUE_ALERT_HOURS))
    if waiting.count() > settings.QUEUE_ALERT_CLIPS or waiting.filter(upload_time__lt=cutoff).exists():
        # Kept only to its block too: a precise time could narrow down when a clip was uploaded.
        raise_alert(Alert.Kind.QUEUE_BACKED_UP, raised_at=four_hour_block(now))


def failed_login(request, email):
    """Count a failed login or a wrong second-factor code for `email`, from any connection.

    Nobody is refused for these (that would let anyone lock a lawyer out); at
    ALERT_FAILED_LOGINS in one window, the admin is alerted about the account, if one has
    that address. Many wrong codes after a right password suggest a stolen password. It
    alerts once per window: more failures after the admin has seen it wait for the next.
    """
    if rate_limits.count(request, rate_limits.FAILED_LOGINS_ANYWHERE, account=email) == settings.ALERT_FAILED_LOGINS:
        account = get_user_model().objects.filter(email=email).first() if email else None
        if account is not None:
            raise_alert(Alert.Kind.FAILED_LOGINS, account)
