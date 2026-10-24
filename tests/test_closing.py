"""Closing cases (SPEC.md §7): by the lawyer, after a long time without use, and on revocation."""

import datetime
import uuid
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import LawyerProfile
from audit.models import Action, AuditEntry, record
from cases.models import Case
from cases.tasks import close_unused_cases
from tests.conftest import log_in_with_second_factor, make_case

pytestmark = pytest.mark.django_db

User = get_user_model()


def make_lawyer(email):
    profile = LawyerProfile.objects.create(user=User.objects.create_user(email), bar='Paris')
    profile.verified_via, profile.vouched_by = 'The bar directory', 'A defence organisation'
    profile.save()
    profile.change_status(LawyerProfile.Status.LAWYER)
    return profile


@pytest.fixture
def lawyer():
    return make_lawyer('lawyer@example.org')


@pytest.fixture
def lawyer_client(lawyer):
    return log_in_with_second_factor(Client(), lawyer.user)


def ago(**delta):
    return timezone.now() - datetime.timedelta(**delta)


def closing_entries():
    return list(AuditEntry.objects.filter(action=Action.CASE_CLOSING).values_list('case', 'lawyer_profile', 'admin'))


def test_a_lawyer_closes_their_case_after_confirming(lawyer, lawyer_client):
    case = make_case(lawyer)
    url = reverse('lawyer:close_case', kwargs={'case_id': case.pk})
    assert lawyer_client.get(url).status_code == 200
    assert Case.objects.get().closed_at is None
    response = lawyer_client.post(url)
    assert response.status_code == 302
    case.refresh_from_db()
    assert case.closed_because == Case.ClosedBecause.LAWYER
    assert closing_entries() == [(case.pk, lawyer.pk, None)]


def test_a_closed_case_cannot_be_closed_again(lawyer, lawyer_client):
    case = make_case(lawyer)
    Case.objects.filter(pk=case.pk).close(Case.ClosedBecause.LAWYER)
    assert lawyer_client.post(reverse('lawyer:close_case', kwargs={'case_id': case.pk})).status_code == 404
    assert len(closing_entries()) == 1


def test_a_lawyer_cannot_close_another_lawyer_s_case(lawyer_client):
    others = make_case(make_lawyer('other@example.org'))
    assert lawyer_client.post(reverse('lawyer:close_case', kwargs={'case_id': others.pk})).status_code == 404
    assert Case.objects.get().closed_at is None and not closing_entries()


def test_closing_a_case_closes_only_open_ones_and_logs_each(lawyer):
    cases = [make_case(lawyer, f'Case {n}') for n in range(3)]
    Case.objects.filter(pk=cases[0].pk).close(Case.ClosedBecause.LAWYER)
    assert Case.objects.filter(lawyer_profile=lawyer).close(Case.ClosedBecause.REVOCATION) == 2
    assert sorted(case for case, _, _ in closing_entries()) == sorted(case.pk for case in cases)


def at(moment, action, lawyer, case, **fields):
    """An audit entry written at `moment`: entries always take the time of writing."""
    with mock.patch('django.utils.timezone.now', return_value=moment):
        return record(action, lawyer, case=case, **fields)


def test_a_case_closes_after_a_year_without_use(lawyer, settings):
    settings.CASE_INACTIVITY_DAYS = 365
    start = timezone.now()
    old, used, recent, never_logged = (make_case(lawyer, label) for label in ('Old', 'Used', 'Recent', 'Unlogged'))
    for case in (old, used):
        at(start, Action.CASE_CREATION, lawyer, case)
    at(start + datetime.timedelta(days=200), Action.CASE_CREATION, lawyer, recent)
    # A search eleven months in keeps "Used" open past the year.
    at(start + datetime.timedelta(days=330), Action.SEARCH, lawyer, used, core_count=0, margin_count=0)
    assert close_unused_cases(now=start + datetime.timedelta(days=366)) == 2
    states = dict(Case.objects.values_list('pk', 'closed_because'))
    assert states == {old.pk: 'inactivity', used.pk: '', recent.pk: '', never_logged.pk: 'inactivity'}
    assert sorted(case for case, _, admin in closing_entries() if admin is None) == sorted([old.pk, never_logged.pk])


@pytest.mark.parametrize('action', [Action.PREVIEW, Action.DOWNLOAD])
def test_previews_and_downloads_count_as_use(lawyer, settings, action):
    settings.CASE_INACTIVITY_DAYS = 365
    start = timezone.now()
    case = make_case(lawyer)
    at(start + datetime.timedelta(days=300), action, lawyer, case, clip=mock.Mock(pk=uuid.uuid4()))
    assert close_unused_cases(now=start + datetime.timedelta(days=366)) == 0


def test_viewing_the_case_page_does_not_count_as_use(lawyer, lawyer_client, settings):
    settings.CASE_INACTIVITY_DAYS = 365
    case = make_case(lawyer)
    lawyer_client.get(reverse('lawyer:case', kwargs={'case_id': case.pk}))
    assert close_unused_cases(now=timezone.now() + datetime.timedelta(days=366)) == 1


def test_the_case_page_shows_the_case_log_and_only_its_own(lawyer, lawyer_client, settings):
    settings.LANGUAGE_CODE = 'en'
    case = make_case(lawyer)
    record(Action.CASE_CREATION, lawyer, case=case)
    record(Action.SEARCH, lawyer, case=case, core_count=3, margin_count=1)
    other = make_case(lawyer, 'Other')
    record(Action.SEARCH, lawyer, case=other, core_count=9, margin_count=8)
    Case.objects.filter(pk=other.pk).close(Case.ClosedBecause.LAWYER)
    page = lawyer_client.get(reverse('lawyer:case', kwargs={'case_id': case.pk})).content.decode()
    assert 'Case creation' in page and 'core matches: 3, margin matches: 1' in page
    assert 'Case closing' not in page and 'core matches: 9' not in page


def test_the_log_of_a_refused_case_names_no_admin(lawyer, lawyer_client, settings):
    settings.LANGUAGE_CODE = 'en'
    admin = User.objects.create_superuser('admin@example.org', first_name='Dominique', last_name='Admin')
    case = make_case(lawyer, starts_at=ago(days=2), ends_at=ago(days=2) + datetime.timedelta(hours=3), approval_reason='Two charges.')
    case.approval.decide(False, admin)
    page = lawyer_client.get(reverse('lawyer:case', kwargs={'case_id': case.pk})).content.decode()
    assert page.index('Refusal of a larger case') < page.index('Case closing')
    assert 'Dominique' not in page and 'admin@example.org' not in page
    assert 'Close this case' not in page


def test_closing_twice_closes_and_logs_once(lawyer):
    case = make_case(lawyer)
    assert Case.objects.filter(pk=case.pk).close(Case.ClosedBecause.LAWYER) == 1
    assert Case.objects.filter(pk=case.pk).close(Case.ClosedBecause.REVOCATION) == 0
    assert Case.objects.get().closed_because == Case.ClosedBecause.LAWYER and len(closing_entries()) == 1


def test_closing_for_no_use_deletes_a_waiting_request_s_reason(lawyer, settings):
    settings.CASE_INACTIVITY_DAYS = 365
    case = make_case(lawyer, starts_at=ago(days=2), ends_at=ago(days=2) + datetime.timedelta(hours=3), approval_reason='Two charges.')
    assert close_unused_cases(now=timezone.now() + datetime.timedelta(days=366)) == 1
    case.refresh_from_db()
    assert case.closed_because == Case.ClosedBecause.INACTIVITY and case.approval.reason == ''


def test_the_nightly_closing_is_a_task_of_the_background_worker():
    from huey.api import PeriodicTask

    from cases import tasks
    assert isinstance(tasks.close_unused_cases_daily.task_class(), PeriodicTask)


def test_closed_cases_are_listed_apart(lawyer, lawyer_client):
    open_case, closed, revoked = make_case(lawyer, 'Still open'), make_case(lawyer, 'Done'), make_case(lawyer, 'Revoked')
    Case.objects.filter(pk=closed.pk).close(Case.ClosedBecause.LAWYER)
    Case.objects.filter(pk=revoked.pk).close(Case.ClosedBecause.REVOCATION)
    other = make_case(make_lawyer('other@example.org'), 'Not mine')
    page = lawyer_client.get(reverse('lawyer:closed_cases')).content.decode()
    # Labels come encrypted (cases.labels): the links tell which cases are listed.
    listed = [case for case in (open_case, closed, revoked, other) if reverse('lawyer:case', kwargs={'case_id': case.pk}) in page]
    assert listed == [closed, revoked]
