import datetime
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.views.debug import ExceptionReporter, SafeExceptionReporterFilter
from django.urls import reverse
from django.utils import timezone

from accounts.models import LawyerProfile
from audit.models import Action, AuditEntry
from cases.models import Case, CaseApproval
from clips import grid
from tests.conftest import BASTILLE, log_in_with_second_factor, make_case

pytestmark = pytest.mark.django_db

User = get_user_model()


def make_lawyer(email):
    user = User.objects.create_user(email)
    profile = LawyerProfile.objects.create(user=user, bar='Paris')
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


# Typed as in the police report: Paris time, as the browser's datetime-local field sends it.
FORM = {
    'label': 'Bastille, 15 June', 'starts_at': '2026-06-15T18:00', 'ends_at': '2026-06-15T19:00',
    'latitude': str(BASTILLE[0]), 'longitude': str(BASTILLE[1]), 'radius_metres': '200',
    'margin_minutes': '10', 'margin_rings': '1',
}


REASON = 'Two charges in the same evening, an hour apart.'


def create(client, **changes):
    return client.post(reverse('lawyer:new_case'), {**FORM, **changes})


def test_a_new_case_starts_with_the_lawyer_s_default_margin(lawyer, lawyer_client):
    lawyer.default_margin_minutes, lawyer.default_margin_rings = 25, 3
    lawyer.save()
    form = lawyer_client.get(reverse('lawyer:new_case')).context['form']
    assert (form['margin_minutes'].value(), form['margin_rings'].value()) == (25, 3)


def test_a_lawyer_creates_a_case_from_a_place_and_a_radius(lawyer, lawyer_client):
    response = create(lawyer_client)
    case = Case.objects.get()
    assert response.status_code == 302 and response['Location'] == reverse('lawyer:case', kwargs={'case_id': case.pk})
    assert (case.lawyer_profile, case.label, case.margin_minutes, case.margin_rings) == (lawyer, 'Bastille, 15 June', 10, 1)
    paris = datetime.timezone(datetime.timedelta(hours=2))
    assert case.starts_at == datetime.datetime(2026, 6, 15, 18, tzinfo=paris)
    assert case.ends_at - case.starts_at == datetime.timedelta(hours=1)
    assert case.area() == grid.cells_around(*BASTILLE, 200) and len(case.area()) == 113


def test_creating_a_case_writes_an_audit_entry(lawyer, lawyer_client):
    create(lawyer_client)
    entry = AuditEntry.objects.get()
    assert (entry.action, entry.lawyer_profile, entry.case, entry.admin) == (
        Action.CASE_CREATION, lawyer, Case.objects.get(), None,
    )


def test_the_typed_place_is_never_sent_back(lawyer_client):
    response = create(lawyer_client, label='')
    assert response.status_code == 400
    page = response.content.decode()
    assert FORM['latitude'] not in page and FORM['longitude'] not in page
    assert FORM['radius_metres'] in page and not Case.objects.exists()


# An unusual place, so that its digits are found nowhere by chance.
ODD_PLACE = {'latitude': '48.85417', 'longitude': '2.36923'}


def test_the_typed_place_is_never_kept_or_logged(lawyer_client, caplog):
    create(lawyer_client, **ODD_PLACE)
    create(lawyer_client, label='', **ODD_PLACE)
    case = Case.objects.get()
    database = '\n'.join(connection.connection.iterdump())
    page = lawyer_client.get(reverse('lawyer:case', kwargs={'case_id': case.pk})).content.decode()
    for kept in (database, page, caplog.text):
        assert '85417' not in kept and '36923' not in kept


def test_the_typed_place_and_reason_are_left_out_of_error_reports(lawyer_client):
    request = create(lawyer_client, label='', reason=REASON).wsgi_request
    shown = SafeExceptionReporterFilter().get_post_parameters(request)
    hidden = SafeExceptionReporterFilter.cleansed_substitute
    assert shown['latitude'] == shown['longitude'] == shown['reason'] == hidden
    assert shown['label'] == ''


def test_a_case_over_the_size_caps_is_refused_with_its_size(lawyer_client, settings):
    settings.LANGUAGE_CODE = 'en'
    response = create(lawyer_client, starts_at='2026-06-15T17:00', ends_at='2026-06-15T19:00')
    assert response.status_code == 400 and not Case.objects.exists() and not AuditEntry.objects.exists()
    page = response.content.decode()
    assert 'this case lasts 140 minutes' in page and 'a case lasts at most 120 minutes' in page


def test_a_case_and_its_audit_entry_are_made_together(lawyer_client, monkeypatch):
    monkeypatch.setattr('cases.views.record', mock.Mock(side_effect=RuntimeError))
    with pytest.raises(RuntimeError):
        create(lawyer_client)
    assert not Case.objects.exists()


@pytest.mark.parametrize('starts_at, ends_at, offset, hours', [
    ('2026-01-15T18:00', '2026-01-15T19:00', 1, 1),
    # Clocks go back at 03:00 on 25 October 2026: the first 02:30 is meant.
    ('2026-10-25T02:30', '2026-10-25T03:30', 2, 2),
    # Clocks go forward at 02:00 on 29 March 2026: 02:30 is read with the winter offset.
    ('2026-03-29T02:30', '2026-03-29T04:00', 1, 0.5),
])
def test_times_are_french_time_whatever_the_clocks_do(lawyer_client, starts_at, ends_at, offset, hours):
    assert create(lawyer_client, starts_at=starts_at, ends_at=ends_at).status_code == 302
    case = Case.objects.get()
    local = datetime.timezone(datetime.timedelta(hours=offset))
    assert case.starts_at == datetime.datetime.fromisoformat(starts_at).replace(tzinfo=local)
    assert case.ends_at - case.starts_at == datetime.timedelta(hours=hours)


@pytest.mark.parametrize('starts_at', ['0001-01-01T00:00', '1999-12-31T23:00', '9999-12-31T22:00', '2099-01-01T00:00'])
def test_dates_far_off_are_refused(lawyer_client, starts_at):
    response = create(lawyer_client, starts_at=starts_at, ends_at='9999-12-31T23:00')
    assert response.status_code == 400 and 'starts_at' in response.context['form'].errors


@pytest.mark.parametrize('typed', [
    '2026-06-15T18:00Z', '2026-06-15T18:00+05:00', '2026-06-15T18:00-03:00', '20260615T1800-0500', '20260615T1800-0000',
])
def test_a_time_with_an_offset_is_refused_not_misread(lawyer_client, typed):
    response = create(lawyer_client, starts_at=typed)
    assert response.status_code == 400 and 'starts_at' in response.context['form'].errors


def test_a_case_beyond_the_ceiling_says_so(lawyer_client, settings):
    settings.LANGUAGE_CODE = 'en'
    response = create(lawyer_client, starts_at='2026-06-14T18:00', ends_at='2026-06-15T19:00')
    assert response.status_code == 400 and not Case.objects.exists()
    assert 'No case can last more than 1440 minutes' in response.content.decode()


def test_a_time_window_must_end_after_it_starts(lawyer_client):
    response = create(lawyer_client, ends_at=FORM['starts_at'])
    assert response.status_code == 400 and 'ends_at' in response.context['form'].errors


@pytest.mark.parametrize('field, value', [
    ('radius_metres', '5001'), ('radius_metres', '-1'), ('margin_rings', '101'), ('margin_minutes', '-1'),
    ('latitude', '91'),
])
def test_out_of_range_numbers_are_refused(lawyer_client, field, value):
    response = create(lawyer_client, **{field: value})
    assert response.status_code == 400 and field in response.context['form'].errors


@pytest.mark.parametrize('field, value', [('radius_metres', '5001'), ('latitude', '91')])
def test_a_bad_place_or_radius_makes_no_cells(lawyer_client, monkeypatch, field, value):
    monkeypatch.setattr(grid, 'cells_around', mock.Mock(side_effect=AssertionError('cells made')))
    assert create(lawyer_client, **{field: value}).status_code == 400


def test_a_lawyer_sees_only_their_own_cases(lawyer, lawyer_client):
    own = make_case(lawyer, 'Mine')
    others = make_case(make_lawyer('other@example.org'), 'Not mine')
    assert lawyer_client.get(reverse('lawyer:case', kwargs={'case_id': own.pk})).status_code == 200
    assert lawyer_client.get(reverse('lawyer:case', kwargs={'case_id': others.pk})).status_code == 404
    home = lawyer_client.get(reverse('lawyer:home')).content.decode()
    assert 'Mine' in home and 'Not mine' not in home


def test_a_case_page_shows_its_window_area_and_margin(lawyer, lawyer_client, settings):
    settings.LANGUAGE_CODE = 'en'
    case = make_case(lawyer, rings=1, margin_minutes=15, margin_rings=2)
    page = lawyer_client.get(reverse('lawyer:case', kwargs={'case_id': case.pk})).content.decode()
    assert '7 cells, about' in page and '15 minutes' in page and '2 rings of cells' in page


def test_a_lawyer_sets_their_default_margin(lawyer, lawyer_client):
    response = lawyer_client.post(reverse('lawyer:default_margin'), {
        'default_margin_minutes': '5', 'default_margin_rings': '0',
    })
    assert response.status_code == 302
    lawyer.refresh_from_db()
    assert (lawyer.default_margin_minutes, lawyer.default_margin_rings) == (5, 0)


def test_an_empty_default_margin_is_refused(lawyer, lawyer_client):
    assert lawyer_client.post(reverse('lawyer:default_margin'), {}).status_code == 400


def test_a_default_margin_is_bounded(lawyer, lawyer_client):
    response = lawyer_client.post(reverse('lawyer:default_margin'), {
        'default_margin_minutes': '10', 'default_margin_rings': '1000',
    })
    assert response.status_code == 400
    lawyer.refresh_from_db()
    assert lawyer.default_margin_rings == 1


# Larger cases (SPEC.md §7): over the size caps, a case waits for the admin's approval.

LARGER = {'starts_at': '2026-06-15T17:00', 'ends_at': '2026-06-15T19:00'}


@pytest.fixture
def admin_client():
    return log_in_with_second_factor(Client(), User.objects.create_superuser('admin@example.org'))


def test_a_case_over_the_caps_with_a_reason_waits_for_approval(lawyer, lawyer_client):
    response = create(lawyer_client, reason=REASON, **LARGER)
    case = Case.objects.get()
    assert response.status_code == 302
    assert case.approval.reason == REASON and case.approval.decided_at is None
    assert case.waiting_for_approval() and not Case.objects.usable().exists()
    assert list(AuditEntry.objects.order_by('pk').values_list('action', flat=True)) == [
        Action.CASE_CREATION, Action.LARGER_CASE_REQUEST,
    ]
    page = lawyer_client.get(reverse('lawyer:case', kwargs={'case_id': case.pk})).content.decode()
    assert "en attente de validation par l'administrateur·rice" in page


def test_a_reason_for_a_case_within_the_caps_is_not_kept(lawyer_client):
    create(lawyer_client, reason=REASON)
    case = Case.objects.get()
    assert not CaseApproval.objects.exists()
    assert Case.objects.usable().get() == case
    assert list(AuditEntry.objects.values_list('action', flat=True)) == [Action.CASE_CREATION]


def test_a_reason_does_not_let_a_case_beyond_the_ceiling_through(lawyer_client):
    response = create(lawyer_client, reason=REASON, starts_at='2026-06-14T18:00', ends_at='2026-06-15T19:00')
    assert response.status_code == 400 and not Case.objects.exists()


@pytest.fixture
def waiting_case(lawyer, lawyer_client):
    create(lawyer_client, reason=REASON, **LARGER)
    return Case.objects.get()


def decide(admin_client, case, approved, note=''):
    return admin_client.post(reverse('manage:case_request', kwargs={'case_id': case.pk}), {
        'approved': 'yes' if approved else 'no', 'note': note,
    })


def test_the_admin_sees_the_window_area_and_reason_but_not_the_label(admin_client, waiting_case):
    listing = admin_client.get(reverse('manage:case_requests')).content.decode()
    assert reverse('manage:case_request', kwargs={'case_id': waiting_case.pk}) in listing
    page = admin_client.get(reverse('manage:case_request', kwargs={'case_id': waiting_case.pk})).content.decode()
    assert REASON in page and waiting_case.label not in page
    # Where the area is, to about 100 m: coarser than the cells, never the typed place.
    assert '48.853, 2.369' in page and FORM['latitude'] not in page and '140 minutes' in page


def test_approving_deletes_the_reason_keeps_the_note_and_logs_it(admin_client, waiting_case, lawyer_client):
    response = decide(admin_client, waiting_case, True, note='Approved for both charges.')
    assert response.status_code == 302
    approval = Case.objects.get().approval
    assert (approval.approved, approval.reason, approval.admin_note) == (True, '', 'Approved for both charges.')
    assert Case.objects.usable().get() == waiting_case
    entry = AuditEntry.objects.latest('pk')
    assert (entry.action, entry.case, entry.lawyer_profile, entry.admin.is_admin) == (
        Action.CASE_APPROVAL, waiting_case, waiting_case.lawyer_profile, True,
    )
    page = lawyer_client.get(reverse('lawyer:case', kwargs={'case_id': waiting_case.pk})).content.decode()
    assert 'Approved for both charges.' in page


def test_refusing_closes_the_case_and_deletes_the_reason(admin_client, waiting_case):
    decide(admin_client, waiting_case, False)
    case = Case.objects.get()
    assert case.closed_because == Case.ClosedBecause.REFUSAL and case.approval.reason == ''
    assert case.approval.approved is False
    assert AuditEntry.objects.latest('pk').action == Action.CASE_REFUSAL


def test_a_request_is_decided_once(admin_client, waiting_case):
    decide(admin_client, waiting_case, True)
    assert decide(admin_client, waiting_case, False).status_code == 404
    assert Case.objects.get().approval.approved is True
    assert AuditEntry.objects.filter(action__in=[Action.CASE_APPROVAL, Action.CASE_REFUSAL]).count() == 1


def test_closing_a_waiting_case_deletes_its_reason_and_withdraws_the_request(admin_client, waiting_case):
    Case.objects.filter(pk=waiting_case.pk).close(Case.ClosedBecause.LAWYER)
    assert Case.objects.get().approval.reason == ''
    assert decide(admin_client, waiting_case, True).status_code == 404
    assert reverse('manage:case_request', kwargs={'case_id': waiting_case.pk}) not in (
        admin_client.get(reverse('manage:case_requests')).content.decode()
    )


def test_the_reason_never_reaches_the_audit_log_or_the_logs(lawyer_client, admin_client, caplog):
    create(lawyer_client, reason=REASON, **LARGER)
    create(lawyer_client, label='', reason=REASON, **LARGER)
    decide(admin_client, Case.objects.get(), True)
    database = '\n'.join(connection.connection.iterdump())
    assert 'Two charges' not in database and 'Two charges' not in caplog.text


def test_the_reason_and_place_are_hidden_in_a_traceback(lawyer_client, monkeypatch):
    monkeypatch.setattr('cases.views.record', mock.Mock(side_effect=RuntimeError))
    with pytest.raises(RuntimeError) as failure:
        create(lawyer_client, reason=REASON, **LARGER)
    frames = ExceptionReporter(None, RuntimeError, failure.value, failure.tb).get_traceback_frames()
    view = next(frame for frame in frames if frame['function'] == 'new_case')
    shown = dict(view['vars'])
    assert shown['fields'] == shown['form'] == SafeExceptionReporterFilter.cleansed_substitute
    # The archive's own frames; the test client's hold what it sent.
    ours = repr([frame['vars'] for frame in frames if '/cases/' in frame['filename']])
    assert 'Two charges' not in ours and '48.8532' not in ours


def test_a_case_cannot_be_closed_by_saving_it(waiting_case):
    case = Case.objects.get(pk=waiting_case.pk)
    case.closed_at, case.closed_because = timezone.now(), Case.ClosedBecause.LAWYER
    with pytest.raises(ValueError, match='close'):
        case.save()
    assert Case.objects.get().closed_at is None and Case.objects.get().approval.reason == REASON
