"""Closing cases left unused (SPEC.md §7), once a day, and email notifications for lawyers,
every hour, in the background worker."""

import datetime
import logging

from django.conf import settings
from django.utils import timezone
from huey import crontab
from huey.contrib.djhuey import db_periodic_task

from .models import Case
from .notifications import send_notifications

logger = logging.getLogger(__name__)


def close_unused_cases(now=None):
    now = now or timezone.now()
    cutoff = now - datetime.timedelta(days=settings.CASE_INACTIVITY_DAYS)
    return Case.objects.filter(pk__in=Case.objects.unused_since(cutoff).values('pk')).close(
        Case.ClosedBecause.INACTIVITY, now=now,
    )


# Each night (the worker's clock is UTC).
@db_periodic_task(crontab(hour='3', minute='17'))
def close_unused_cases_daily():
    closed = close_unused_cases()
    if closed:
        # How many only: never which cases.
        logger.info('Closed %s cases left unused.', closed)


# Every hour: a message's 4-hour block ends on the hour.
@db_periodic_task(crontab(minute='2'))
def send_notifications_hourly():
    sent = send_notifications()
    if sent:
        # How many only: never to whom.
        logger.info('Sent %s notification emails.', sent)
