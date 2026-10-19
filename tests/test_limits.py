from unittest import mock

import pytest
from django.db import OperationalError, connection
from django.test import Client

from audit import limits
from audit.models import LIMIT_SETTINGS, Action, AuditEntry, record
from clips import tasks

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('limits_check')]

DEFAULTS = {
    'CASE_WINDOW_CEILING_MINUTES': 1440, 'CASE_AREA_CEILING_METRES': 10_000,
    'CASE_WINDOW_CAP_MINUTES': 120, 'CASE_AREA_CAP_METRES': 2_000,
    'ALERT_FAILED_LOGINS': 30, 'QUEUE_ALERT_CLIPS': 20, 'QUEUE_ALERT_HOURS': 2,
    'ALERT_DOWNLOADS': 20, 'ALERT_DOWNLOADS_MINUTES': 60, 'ALERT_CASES': 5, 'ALERT_CASES_HOURS': 24,
    'ALERT_RAISE_MAX_DAYS': 3,
}


@pytest.fixture(autouse=True)
def default_limits(settings):
    """The defaults, whatever a development .env sets."""
    for name, number in DEFAULTS.items():
        setattr(settings, name, number)


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


def test_a_failed_check_answers_no_request_and_is_tried_again(monkeypatch):
    client = Client()
    monkeypatch.setattr(limits, '_record_if_changed', mock.Mock(side_effect=RuntimeError('database down')))
    with pytest.raises(RuntimeError):
        client.get('/')
    # Back to the real check: undo() also undoes the fixtures, whose originals are the real one.
    monkeypatch.undo()
    assert client.get('/').status_code == 200
    assert len(changes()) == 1


def test_a_locked_database_is_tried_again(monkeypatch):
    real = limits._record_if_changed
    attempts = mock.Mock(side_effect=[OperationalError('database is locked'), None])
    monkeypatch.setattr(limits, '_record_if_changed', lambda: attempts() or real())
    monkeypatch.setattr(limits.time, 'sleep', lambda seconds: None)
    limits.record_if_changed()
    assert attempts.call_count == 2 and len(changes()) == 1


def test_the_background_worker_checks_when_it_starts():
    tasks.record_changed_limits()
    assert changes() == [{'before': None, 'after': DEFAULTS}]


@pytest.mark.parametrize('limits_given', [
    {'before': None, 'after': {'CASE_AREA_CAP_METRES': 'two thousand'}},
    {'before': None, 'after': {'a client name': 1}},
    {'before': None, 'after': {'DUPONT_JEAN': 1}},
    {'before': None, 'after': {'CASE_AREA_CAP_METRES': True}},
    {'before': 'text', 'after': {}},
    {'after': {}},
    {'before': None, 'after': {}, 'note': 'text'},
    'text',
])
def test_a_change_of_limits_holds_only_setting_names_and_whole_numbers(limits_given):
    with pytest.raises(ValueError):
        record(Action.LIMITS_CHANGE, None, limits=limits_given)
    assert not AuditEntry.objects.exists()


def test_a_database_that_stays_locked_is_given_up_on(monkeypatch):
    attempts = mock.Mock(side_effect=OperationalError('database is locked'))
    monkeypatch.setattr(limits, '_record_if_changed', attempts)
    monkeypatch.setattr(limits.time, 'sleep', lambda seconds: None)
    with pytest.raises(OperationalError):
        limits.record_if_changed()
    assert attempts.call_count == limits.ATTEMPTS


def test_other_database_errors_are_not_tried_again(monkeypatch):
    attempts = mock.Mock(side_effect=OperationalError('no such table: audit_auditentry'))
    monkeypatch.setattr(limits, '_record_if_changed', attempts)
    with pytest.raises(OperationalError):
        limits.record_if_changed()
    assert attempts.call_count == 1


def test_a_malformed_earlier_row_does_not_stop_the_check():
    entry = record(Action.LIMITS_CHANGE, None, limits={'before': None, 'after': {}})
    # As if written past the model, with raw SQL.
    with connection.cursor() as cursor:
        cursor.execute('UPDATE audit_auditentry SET limits = %s WHERE id = %s', ['[1]', entry.pk])
    limits.record_if_changed()
    assert changes()[-1] == {'before': None, 'after': DEFAULTS}


def test_every_tracked_setting_exists_and_is_a_whole_number(settings):
    assert all(type(getattr(settings, name)) is int for name in LIMIT_SETTINGS)


def test_a_setting_added_since_the_last_start_is_recorded_as_a_change():
    older = {name: number for name, number in DEFAULTS.items() if not name.startswith('ALERT_')}
    record(Action.LIMITS_CHANGE, None, limits={'before': None, 'after': older})
    limits.record_if_changed()
    assert changes()[-1] == {'before': older, 'after': DEFAULTS}
