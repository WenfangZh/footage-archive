"""An audit entry whenever the archive starts with different limits (SPEC.md §7).

The size caps and alert limits come from environment variables, which whoever runs the
server can change; the ceilings are in the code. On its first request, each process
compares them with the numbers in the latest change of limits in the audit log, and
records the change if they differ. Several processes starting at once may each record the
same change: an extra entry, never a missed one.
"""

import threading

from django.conf import settings
from django.db import transaction

from .models import Action, AuditEntry, record

# Setting names only: their values are numbers.
LIMIT_SETTINGS = [
    'CASE_WINDOW_CEILING_MINUTES', 'CASE_AREA_CEILING_METRES', 'CASE_WINDOW_CAP_MINUTES', 'CASE_AREA_CAP_METRES',
]


def current_limits():
    return {name: getattr(settings, name) for name in LIMIT_SETTINGS}


def record_if_changed():
    """Write a change of limits to the audit log if they differ from the last one recorded."""
    current = current_limits()
    with transaction.atomic():
        last = AuditEntry.objects.filter(action=Action.LIMITS_CHANGE).order_by('-at', '-pk').first()
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
