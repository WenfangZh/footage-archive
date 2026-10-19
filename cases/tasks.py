"""Closing cases left unused (SPEC.md §7), once a day in the background worker."""

import datetime

from django.conf import settings
from django.utils import timezone
from huey import crontab
from huey.contrib.djhuey import db_periodic_task

from .models import Case


def close_unused_cases(now=None):
    now = now or timezone.now()
    cutoff = now - datetime.timedelta(days=settings.CASE_INACTIVITY_DAYS)
    return Case.objects.filter(pk__in=Case.objects.unused_since(cutoff).values('pk')).close(
        Case.ClosedBecause.INACTIVITY, now=now,
    )


@db_periodic_task(crontab(hour='3', minute='17'))
def close_unused_cases_daily():
    close_unused_cases()
