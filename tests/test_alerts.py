import datetime
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.models import LawyerProfile
from alerts.checks import check_ingest_queue
from alerts.models import Alert
from clips import tasks
from clips.models import Clip
from tests.conftest import log_in_with_second_factor

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = 'a long test passphrase'
NOW = datetime.datetime(2026, 6, 15, 21, 30, tzinfo=datetime.UTC)


def waiting_clip(upload_time):
    return Clip.objects.create(code_hash=uuid.uuid4().hex * 2, upload_time=upload_time)


@pytest.fixture
def lawyer():
    user = User.objects.create_user('lawyer@example.org', PASSWORD)
    profile = LawyerProfile.objects.create(user=user, bar='Paris')
    profile.verified_via, profile.vouched_by = 'The bar directory', 'A defence organisation'
    profile.save()
    profile.change_status(LawyerProfile.Status.LAWYER)
    return user


def test_a_short_queue_raises_no_alert():
    waiting_clip(NOW - datetime.timedelta(minutes=10))
    check_ingest_queue(now=NOW)
    assert not Alert.objects.exists()


def test_too_many_waiting_clips_raise_an_alert(settings):
    settings.QUEUE_ALERT_CLIPS = 3
    for _ in range(4):
        waiting_clip(NOW)
    check_ingest_queue(now=NOW)
    assert Alert.objects.get().kind == Alert.Kind.QUEUE_BACKED_UP


def test_a_clip_waiting_long_after_its_block_raises_an_alert(settings):
    settings.QUEUE_ALERT_HOURS = 2
    # Its block, 12h to 16h Paris time (10h to 14h UTC), ended 7.5 hours before NOW.
    waiting_clip(datetime.datetime(2026, 6, 15, 12, 0, tzinfo=datetime.UTC))
    check_ingest_queue(now=NOW)
    assert Alert.objects.get().kind == Alert.Kind.QUEUE_BACKED_UP


def test_a_clip_whose_block_has_not_long_ended_raises_none(settings):
    settings.QUEUE_ALERT_HOURS = 2
    # Its block (Paris time) has not even ended yet.
    waiting_clip(NOW - datetime.timedelta(minutes=30))
    check_ingest_queue(now=NOW)
    assert not Alert.objects.exists()


def test_finished_clips_do_not_count(settings):
    settings.QUEUE_ALERT_CLIPS = 1
    for _ in range(3):
        clip = waiting_clip(NOW - datetime.timedelta(days=3))
        Clip.objects.filter(pk=clip.pk).update(status=Clip.Status.READY)
    check_ingest_queue(now=NOW)
    assert not Alert.objects.exists()


def test_an_unseen_alert_is_not_raised_again(settings):
    settings.QUEUE_ALERT_CLIPS = 1
    for _ in range(2):
        waiting_clip(NOW)
    check_ingest_queue(now=NOW)
    check_ingest_queue(now=NOW)
    assert Alert.objects.count() == 1
    Alert.objects.update(seen_at=timezone.now())
    check_ingest_queue(now=NOW)
    assert Alert.objects.count() == 2


def test_the_worker_checks_the_queue_on_its_schedule(settings):
    settings.QUEUE_ALERT_CLIPS = 1
    for _ in range(2):
        waiting_clip(timezone.now())
    tasks.delete_unconfirmed_originals.call_local()
    assert Alert.objects.filter(kind=Alert.Kind.QUEUE_BACKED_UP).exists()


def test_many_failed_logins_from_anywhere_alert_the_admin_without_locking_anyone_out(lawyer, settings):
    settings.ALERT_FAILED_LOGINS = 3
    for number in range(3):
        Client(REMOTE_ADDR=f'192.0.2.{number}').post(
            reverse('accounts:login'), {'username': 'lawyer@example.org', 'password': 'a wrong passphrase'},
        )
    alert = Alert.objects.get()
    assert alert.kind == Alert.Kind.FAILED_LOGINS and alert.account == lawyer
    response = Client(REMOTE_ADDR='192.0.2.9').post(
        reverse('accounts:login'), {'username': 'lawyer@example.org', 'password': PASSWORD},
    )
    assert response.status_code == 302


def test_wrong_second_factor_codes_count_towards_the_alert(lawyer, settings):
    settings.ALERT_FAILED_LOGINS = 2
    TOTPDevice.objects.create(user=lawyer, name='authenticator app', confirmed=True)
    for number in range(2):
        client = Client(REMOTE_ADDR=f'192.0.2.{number}')
        client.force_login(lawyer)
        client.post(reverse('accounts:two_factor_verify'), {'code': 'abcdefgh'})
    assert Alert.objects.get().account == lawyer


def test_failed_logins_for_an_unknown_address_raise_no_alert(settings):
    settings.ALERT_FAILED_LOGINS = 1
    Client().post(reverse('accounts:login'), {'username': 'nobody@example.org', 'password': 'a wrong passphrase'})
    assert not Alert.objects.exists()


def test_the_admin_sees_unseen_alerts_and_can_mark_them_seen():
    Alert.objects.create(kind=Alert.Kind.QUEUE_BACKED_UP)
    admin_client = log_in_with_second_factor(Client(), User.objects.create_superuser('admin@example.org'))
    page = admin_client.get(reverse('manage:home')).content.decode()
    assert 'La file de préparation est engorgée' in page
    alert = Alert.objects.get()
    admin_client.post(reverse('manage:alert_seen', args=[alert.pk]))
    alert.refresh_from_db()
    assert alert.seen_at is not None
    assert 'engorgée' not in admin_client.get(reverse('manage:home')).content.decode()


@pytest.mark.parametrize('upload, now, expected', [
    # The block 00h-04h Paris time (22:00 to 02:00 UTC in summer): 2 hours after it is 04:00 UTC.
    (datetime.datetime(2026, 6, 15, 1, 0, tzinfo=datetime.UTC), datetime.datetime(2026, 6, 15, 3, 59, tzinfo=datetime.UTC), False),
    (datetime.datetime(2026, 6, 15, 1, 0, tzinfo=datetime.UTC), datetime.datetime(2026, 6, 15, 4, 0, tzinfo=datetime.UTC), True),
    # 25 October 2026: the 00h-04h block lasts 5 hours (22:00 to 03:00 UTC).
    (datetime.datetime(2026, 10, 25, 2, 59, tzinfo=datetime.UTC), datetime.datetime(2026, 10, 25, 4, 0, tzinfo=datetime.UTC), False),
    (datetime.datetime(2026, 10, 25, 2, 59, tzinfo=datetime.UTC), datetime.datetime(2026, 10, 25, 5, 0, tzinfo=datetime.UTC), True),
    # 29 March 2026: the 00h-04h block lasts 3 hours (23:00 to 02:00 UTC).
    (datetime.datetime(2026, 3, 29, 0, 30, tzinfo=datetime.UTC), datetime.datetime(2026, 3, 29, 3, 59, tzinfo=datetime.UTC), False),
    (datetime.datetime(2026, 3, 29, 0, 30, tzinfo=datetime.UTC), datetime.datetime(2026, 3, 29, 4, 0, tzinfo=datetime.UTC), True),
])
def test_the_wait_is_counted_from_the_real_end_of_the_block_even_when_clocks_change(settings, upload, now, expected):
    settings.QUEUE_ALERT_HOURS = 2
    waiting_clip(upload)
    check_ingest_queue(now=now)
    assert Alert.objects.exists() == expected


def test_exactly_the_limit_of_waiting_clips_raises_no_alert(settings):
    settings.QUEUE_ALERT_CLIPS = 3
    for _ in range(3):
        waiting_clip(NOW)
    check_ingest_queue(now=NOW)
    assert not Alert.objects.exists()


def test_a_queue_alert_keeps_its_time_only_to_the_block(settings):
    settings.QUEUE_ALERT_CLIPS = 1
    for _ in range(2):
        waiting_clip(NOW)
    check_ingest_queue(now=NOW)
    # 21:30 UTC is 23:30 Paris time, in the 20h-24h block.
    assert Alert.objects.get().raised_at == datetime.datetime(2026, 6, 15, 18, 0, tzinfo=datetime.UTC)


def test_one_failure_short_of_the_threshold_raises_nothing(lawyer, settings):
    settings.ALERT_FAILED_LOGINS = 3
    for number in range(2):
        Client(REMOTE_ADDR=f'192.0.2.{number}').post(
            reverse('accounts:login'), {'username': 'lawyer@example.org', 'password': 'a wrong passphrase'},
        )
    assert not Alert.objects.exists()


def test_wrong_passwords_and_wrong_codes_add_up(lawyer, settings):
    settings.ALERT_FAILED_LOGINS = 2
    Client(REMOTE_ADDR='192.0.2.1').post(
        reverse('accounts:login'), {'username': 'lawyer@example.org', 'password': 'a wrong passphrase'},
    )
    TOTPDevice.objects.create(user=lawyer, name='authenticator app', confirmed=True)
    client = Client(REMOTE_ADDR='192.0.2.2')
    client.force_login(lawyer)
    client.post(reverse('accounts:two_factor_verify'), {'code': 'abcdefgh'})
    assert Alert.objects.get().account == lawyer


def test_an_alert_about_a_closed_account_shows_its_former_address(lawyer):
    Alert.objects.create(kind=Alert.Kind.FAILED_LOGINS, account=lawyer)
    lawyer.former_email, lawyer.email = lawyer.email, ''
    lawyer.save()
    admin_client = log_in_with_second_factor(Client(), User.objects.create_superuser('admin@example.org'))
    alerts = admin_client.get(reverse('manage:home')).content.decode().split('<h2>')[1]
    assert '(lawyer@example.org)' in alerts and 'None' not in alerts
