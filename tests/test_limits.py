import pytest
from django.test import Client

from audit import limits
from audit.models import Action, AuditEntry

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('limits_check')]

DEFAULTS = {
    'CASE_WINDOW_CEILING_MINUTES': 1440, 'CASE_AREA_CEILING_METRES': 10_000,
    'CASE_WINDOW_CAP_MINUTES': 120, 'CASE_AREA_CAP_METRES': 2_000,
}


def changes():
    return [entry.limits for entry in AuditEntry.objects.filter(action=Action.LIMITS_CHANGE).order_by('pk')]


def test_the_first_start_records_the_limits():
    limits.record_if_changed()
    assert changes() == [{'before': None, 'after': DEFAULTS}]
    entry = AuditEntry.objects.get()
    assert (entry.lawyer_profile, entry.admin, entry.case) == (None, None, None)


def test_a_start_with_the_same_limits_records_nothing():
    limits.record_if_changed()
    limits.record_if_changed()
    assert len(changes()) == 1


def test_a_start_with_other_limits_records_the_numbers_before_and_after(settings):
    limits.record_if_changed()
    settings.CASE_AREA_CAP_METRES = 1_500
    limits.record_if_changed()
    assert changes()[-1] == {'before': DEFAULTS, 'after': {**DEFAULTS, 'CASE_AREA_CAP_METRES': 1_500}}
    settings.CASE_AREA_CAP_METRES = 2_000
    limits.record_if_changed()
    assert changes()[-1] == {'before': {**DEFAULTS, 'CASE_AREA_CAP_METRES': 1_500}, 'after': DEFAULTS}
    assert len(changes()) == 3


def test_each_process_checks_once_before_its_first_request(settings):
    client = Client()
    client.get('/')
    assert len(changes()) == 1
    settings.CASE_WINDOW_CAP_MINUTES = 60
    client.get('/')
    # The same process: the change is found at the next start, not now.
    assert len(changes()) == 1
    Client().get('/')
    assert changes()[-1]['after']['CASE_WINDOW_CAP_MINUTES'] == 60
