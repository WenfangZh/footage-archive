import datetime

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from accounts.models import LawyerProfile
from audit.models import Action, AuditEntry
from cases.models import Case
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
    assert case.area() == grid.cells_around(*BASTILLE, 200) and len(case.area()) == 61


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


def test_the_typed_place_is_never_kept(lawyer_client):
    create(lawyer_client)
    case = Case.objects.get()
    stored = [str(value) for value in Case.objects.filter(pk=case.pk).values().get().values()]
    assert not any(FORM['latitude'][:6] in value or FORM['longitude'][:5] in value for value in stored)
    page = lawyer_client.get(reverse('lawyer:case', kwargs={'case_id': case.pk})).content.decode()
    assert FORM['latitude'][:6] not in page and FORM['longitude'][:5] not in page


def test_the_typed_place_is_left_out_of_error_reports(lawyer_client):
    request = create(lawyer_client, label='').wsgi_request
    assert set(request.sensitive_post_parameters) == {'latitude', 'longitude'}


def test_a_case_over_the_size_caps_is_refused_with_its_size(lawyer_client):
    response = create(lawyer_client, starts_at='2026-06-15T17:00', ends_at='2026-06-15T19:00')
    assert response.status_code == 400 and not Case.objects.exists() and not AuditEntry.objects.exists()
    page = response.content.decode()
    assert '140' in page and '120' in page


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
def test_out_of_range_numbers_are_refused_before_any_cell_is_made(lawyer_client, field, value):
    response = create(lawyer_client, **{field: value})
    assert response.status_code == 400 and field in response.context['form'].errors


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


def test_a_default_margin_is_bounded(lawyer, lawyer_client):
    response = lawyer_client.post(reverse('lawyer:default_margin'), {
        'default_margin_minutes': '10', 'default_margin_rings': '1000',
    })
    assert response.status_code == 400
    lawyer.refresh_from_db()
    assert lawyer.default_margin_rings == 1
