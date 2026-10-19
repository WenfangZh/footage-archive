"""Alerts on unusual lawyer activity, and the admin's temporary raises (SPEC.md §7)."""

import datetime
import uuid
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import LawyerProfile
from alerts.checks import activity_limit, check_activity
from alerts.models import Alert, AlertLimitRaise
from audit.models import Action, AuditEntry, record
from tests.conftest import log_in_with_second_factor, make_case

pytestmark = pytest.mark.django_db

User = get_user_model()
DOWNLOADS, CASES = AlertLimitRaise.Activity.DOWNLOADS, AlertLimitRaise.Activity.CASES


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
def case(lawyer):
    return make_case(lawyer)


@pytest.fixture(autouse=True)
def limits(settings):
    settings.ALERT_DOWNLOADS, settings.ALERT_DOWNLOADS_MINUTES = 3, 60
    settings.ALERT_CASES, settings.ALERT_CASES_HOURS = 2, 24
    settings.ALERT_RAISE_MAX_DAYS = 3


def download(case, at=None):
    with mock.patch('django.utils.timezone.now', return_value=at or timezone.now()):
        record(Action.DOWNLOAD, case.lawyer_profile, case=case, clip=mock.Mock(pk=uuid.uuid4()))


def alerts(kind=Alert.Kind.MANY_DOWNLOADS):
    return list(Alert.objects.filter(kind=kind).values_list('account', flat=True))


def test_many_downloads_in_a_short_time_alert_the_admin_once(lawyer, case):
    for _ in range(2):
        download(case)
        check_activity(lawyer, DOWNLOADS)
    assert alerts() == []
    for _ in range(3):
        download(case)
        check_activity(lawyer, DOWNLOADS)
    assert alerts() == [lawyer.user.pk]


def test_downloads_spread_out_raise_no_alert(lawyer, case):
    for hours in (3, 2, 0):
        download(case, at=timezone.now() - datetime.timedelta(hours=hours))
    check_activity(lawyer, DOWNLOADS)
    assert alerts() == []


def test_downloads_are_counted_per_lawyer(lawyer, case):
    other_case = make_case(make_lawyer('other@example.org'))
    for each in (case, case, other_case):
        download(each)
    check_activity(lawyer, DOWNLOADS)
    assert alerts() == []


def test_a_new_alert_after_the_span_has_passed(lawyer, case):
    earlier = timezone.now() - datetime.timedelta(hours=2)
    for _ in range(3):
        download(case, at=earlier)
    check_activity(lawyer, DOWNLOADS, now=earlier)
    for _ in range(3):
        download(case)
    check_activity(lawyer, DOWNLOADS)
    # The first, still unseen, stands for both.
    assert len(alerts()) == 1
    Alert.objects.update(seen_at=timezone.now())
    check_activity(lawyer, DOWNLOADS)
    assert len(alerts()) == 2


def test_many_cases_created_alert_the_admin(lawyer):
    for label in ('One', 'Two'):
        record(Action.CASE_CREATION, lawyer, case=make_case(lawyer, label))
    check_activity(lawyer, CASES)
    assert alerts(Alert.Kind.MANY_CASES) == [lawyer.user.pk]


def test_a_raise_applies_until_it_ends(lawyer, case):
    now = timezone.now()
    AlertLimitRaise.objects.create(lawyer_profile=lawyer, activity=DOWNLOADS, limit=10, until=now + datetime.timedelta(days=1))
    assert activity_limit(lawyer, DOWNLOADS) == 10 and activity_limit(lawyer, CASES) == 2
    for _ in range(5):
        download(case)
    check_activity(lawyer, DOWNLOADS)
    assert alerts() == []
    assert activity_limit(lawyer, DOWNLOADS, now=now + datetime.timedelta(days=2)) == 3


def test_the_views_check_after_logging(lawyer, settings):
    settings.ALERT_CASES = 1
    client = log_in_with_second_factor(Client(), lawyer.user)
    client.post(reverse('lawyer:new_case'), {
        'label': 'A case', 'starts_at': '2026-06-15T18:00', 'ends_at': '2026-06-15T19:00',
        'latitude': '48.8532', 'longitude': '2.3692', 'radius_metres': '50', 'margin_minutes': '10', 'margin_rings': '1',
    })
    assert alerts(Alert.Kind.MANY_CASES) == [lawyer.user.pk]


# The admin's raise page.

@pytest.fixture
def admin():
    return User.objects.create_superuser('admin@example.org')


@pytest.fixture
def admin_client(admin):
    return log_in_with_second_factor(Client(), admin)


def raise_limit(client, lawyer, **data):
    return client.post(reverse('manage:raise_limit', kwargs={'profile_id': lawyer.pk}), {
        'activity': 'downloads', 'limit': '10', 'days': '2', **data,
    })


def test_the_admin_raises_a_limit_for_a_few_days_and_it_is_logged(admin, admin_client, lawyer):
    assert raise_limit(admin_client, lawyer).status_code == 302
    raised = AlertLimitRaise.objects.get()
    assert (raised.lawyer_profile, raised.activity, raised.limit) == (lawyer, DOWNLOADS, 10)
    assert datetime.timedelta(days=2) - (raised.until - timezone.now()) < datetime.timedelta(minutes=1)
    entry = AuditEntry.objects.get(action=Action.ALERT_LIMIT_RAISE)
    assert (entry.admin, entry.lawyer_profile) == (admin, lawyer)
    assert entry.limits == {'before': {'ALERT_DOWNLOADS': 3}, 'after': {'ALERT_DOWNLOADS': 10}, 'days': 2}


@pytest.mark.parametrize('data', [{'days': '4'}, {'days': '0'}, {'limit': '3'}, {'limit': '31'}, {'activity': 'searches'}])
def test_a_raise_is_bounded(admin_client, lawyer, data):
    assert raise_limit(admin_client, lawyer, **data).status_code == 400
    assert not AlertLimitRaise.objects.exists() and not AuditEntry.objects.exists()


def test_the_longest_raise_is_a_setting(admin_client, lawyer, settings):
    settings.ALERT_RAISE_MAX_DAYS = 5
    assert raise_limit(admin_client, lawyer, days='5').status_code == 302


def test_a_raise_is_never_changed(lawyer):
    raised = AlertLimitRaise.objects.create(
        lawyer_profile=lawyer, activity=DOWNLOADS, limit=10, until=timezone.now() + datetime.timedelta(days=1),
    )
    raised.limit = 100
    with pytest.raises(ValueError):
        raised.save()


def test_only_a_current_lawyer_s_limit_can_be_raised(admin_client):
    applicant = LawyerProfile.objects.create(user=User.objects.create_user('applicant@example.org'), bar='Lyon')
    assert raise_limit(admin_client, applicant).status_code == 404


def test_one_alert_per_span_even_once_the_admin_has_seen_it(lawyer, case):
    for _ in range(3):
        download(case)
    check_activity(lawyer, DOWNLOADS)
    Alert.objects.update(seen_at=timezone.now())
    download(case)
    check_activity(lawyer, DOWNLOADS)
    assert len(alerts()) == 1
    later = timezone.now() + datetime.timedelta(minutes=61)
    for _ in range(3):
        download(case, at=later)
    check_activity(lawyer, DOWNLOADS, now=later)
    assert len(alerts()) == 2


def test_the_highest_raise_in_force_applies(lawyer):
    until = timezone.now() + datetime.timedelta(days=1)
    for limit in (8, 12, 10):
        AlertLimitRaise.objects.create(lawyer_profile=lawyer, activity=DOWNLOADS, limit=limit, until=until)
    assert activity_limit(lawyer, DOWNLOADS) == 12


def test_downloads_through_the_page_are_checked_and_resumes_are_not_counted(lawyer, settings, tmp_path):
    from tests.test_clip_pages import make_clip_for

    settings.ALERT_DOWNLOADS = 2
    case, clip = make_clip_for(lawyer, tmp_path)
    client = log_in_with_second_factor(Client(), lawyer.user)
    url = reverse('lawyer:download', kwargs={'case_id': case.pk, 'clip_id': clip.pk})
    client.get(url, HTTP_RANGE='bytes=0-9')
    client.get(url, HTTP_RANGE='bytes=10-')
    assert alerts() == [] and AuditEntry.objects.filter(action=Action.DOWNLOAD).count() == 1
    client.get(url)
    assert alerts() == [lawyer.user.pk]


def test_a_failed_check_leaves_no_case_behind(lawyer, monkeypatch):
    from cases.models import Case
    monkeypatch.setattr('cases.views.check_activity', mock.Mock(side_effect=RuntimeError))
    client = log_in_with_second_factor(Client(), lawyer.user)
    with pytest.raises(RuntimeError):
        client.post(reverse('lawyer:new_case'), {
            'label': 'A case', 'starts_at': '2026-06-15T18:00', 'ends_at': '2026-06-15T19:00',
            'latitude': '48.8532', 'longitude': '2.3692', 'radius_metres': '50', 'margin_minutes': '10',
            'margin_rings': '1',
        })
    assert not Case.objects.exists() and not AuditEntry.objects.exists()
