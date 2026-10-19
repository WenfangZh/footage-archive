"""An audit entry whenever the archive starts with different limits (SPEC.md §7).

The size caps and alert limits come from environment variables, which whoever runs the
server can change; the ceilings are in the code. Each process, before answering its
first request, and the background worker, when it starts, compare them with the numbers
in the latest change of limits in the audit log, and record the change if they differ.
Until the check succeeds, a web process answers no request: none is ever answered under
limits that were not recorded.
"""

import threading
import time

from django.conf import settings
from django.db import OperationalError, transaction

from .models import Action, AuditEntry, record

# Setting names only: their values are whole numbers.
LIMIT_SETTINGS = [
    # Size caps on cases, and the ceilings above them.
    'CASE_WINDOW_CEILING_MINUTES', 'CASE_AREA_CEILING_METRES', 'CASE_WINDOW_CAP_MINUTES', 'CASE_AREA_CAP_METRES',
    # Alert limits.
    'ALERT_FAILED_LOGINS', 'QUEUE_ALERT_CLIPS', 'QUEUE_ALERT_HOURS',
]
# Processes starting together may each try to write; on SQLite one then fails at once
# with "database is locked" instead of waiting, so it tries again.
ATTEMPTS = 5


def current_limits():
    return {name: getattr(settings, name) for name in LIMIT_SETTINGS}


def record_if_changed():
    """Write a change of limits to the audit log if they differ from the last one recorded."""
    for attempt in range(ATTEMPTS):
        try:
            return _record_if_changed()
        except OperationalError:
            if attempt == ATTEMPTS - 1:
                raise
            time.sleep(0.1 * (attempt + 1))


def _record_if_changed():
    current = current_limits()
    with transaction.atomic():
        last = AuditEntry.objects.filter(action=Action.LIMITS_CHANGE).order_by('-pk').first()
        before = last.limits['after'] if last else None
        if before != current:
            record(Action.LIMITS_CHANGE, None, limits={'before': before, 'after': current})


class LimitsCheckMiddleware:
    """Checks the limits once per process, before its first request is answered."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.checked = False
        self.lock = threading.Lock()

    def __call__(self, request):
        if not self.checked:
            with self.lock:
                if not self.checked:
                    record_if_changed()
                    self.checked = True
        return self.get_response(request)
