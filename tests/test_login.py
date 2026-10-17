from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.urls import reverse
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.models import LawyerProfile

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = 'a long test passphrase'
LOGIN_URL = reverse('accounts:login')
ENGLISH = {'accept_language': 'en'}
# Documentation addresses (RFC 5737).
ADDRESSES = ['192.0.2.1', '192.0.2.2', '192.0.2.3']


@pytest.fixture
def lawyer():
    user = User.objects.create_user('lawyer@example.org', PASSWORD)
    profile = LawyerProfile.objects.create(user=user, bar='Paris')
    profile.verified_via = 'The bar directory'
    profile.vouched_by = 'A defence organisation'
    profile.save()
    profile.change_status(LawyerProfile.Status.LAWYER)
    return user


@pytest.fixture
def applicant():
    user = User.objects.create_user('applicant@example.org', PASSWORD)
    LawyerProfile.objects.create(user=user, bar='Paris')
    return user


def log_in(client, email, password=PASSWORD):
    return client.post(LOGIN_URL, {'username': email, 'password': password})


def test_the_login_page_is_in_french_and_english(client):
    assert 'Se connecter' in client.get(LOGIN_URL).content.decode()
    assert 'Log in' in client.get(LOGIN_URL, headers={'accept-language': 'en'}).content.decode()


def pass_second_factor(client, user):
    device = TOTPDevice.objects.create(user=user, name='authenticator app', confirmed=True)
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()


def test_a_password_alone_leads_to_setting_up_the_second_factor(client, lawyer):
    response = log_in(client, 'Lawyer@Example.org')
    assert response.status_code == 302
    assert client.get(response['Location'])['Location'].startswith(reverse('accounts:two_factor_setup'))


def test_a_lawyer_lands_on_their_pages(client, lawyer):
    log_in(client, 'lawyer@example.org')
    pass_second_factor(client, lawyer)
    assert client.get(reverse('accounts:home'))['Location'] == reverse('lawyer:home')


def test_the_admin_lands_on_the_admin_pages(client):
    admin = User.objects.create_superuser('admin@example.org', PASSWORD)
    log_in(client, 'admin@example.org')
    pass_second_factor(client, admin)
    assert client.get(reverse('accounts:home'))['Location'] == reverse('manage:home')


def test_an_applicant_lands_on_a_page_that_shows_nothing(client, applicant):
    log_in(client, 'applicant@example.org')
    pass_second_factor(client, applicant)
    page = client.get(reverse('accounts:home'))
    assert page.status_code == 200
    assert [template.name for template in page.templates][0] == 'accounts/applicant.html'


def test_a_wrong_password_logs_nobody_in(client, lawyer):
    response = log_in(client, 'lawyer@example.org', 'a wrong passphrase')
    assert response.status_code == 200
    assert 'Le mot de passe tient compte des majuscules' in response.content.decode()
    assert '_auth_user_id' not in client.session


def test_an_inactive_account_cannot_log_in(client, lawyer):
    lawyer.is_active = False
    lawyer.save()
    log_in(client, 'lawyer@example.org')
    assert '_auth_user_id' not in client.session


def test_logging_out_ends_the_session(client, lawyer):
    log_in(client, 'lawyer@example.org')
    assert client.get(reverse('accounts:logout')).status_code == 405
    client.post(reverse('accounts:logout'))
    assert '_auth_user_id' not in client.session
    assert client.get(reverse('lawyer:home')).status_code == 302


def test_failed_logins_over_the_limit_stop_all_password_checks(lawyer, settings):
    settings.RATE_LIMIT_FAILED_LOGINS = 3
    client = Client(REMOTE_ADDR=ADDRESSES[0])
    for number in range(3):
        log_in(client, f'someone{number}@example.org', 'a wrong passphrase')
    with mock.patch('django.contrib.auth.forms.authenticate') as authenticate:
        response = log_in(client, 'lawyer@example.org')
    assert response.status_code == 429
    authenticate.assert_not_called()
    assert '_auth_user_id' not in client.session


def test_failed_logins_for_one_account_stop_its_logins_from_that_connection(lawyer, applicant, settings):
    settings.RATE_LIMIT_FAILED_LOGINS_FOR_ACCOUNT = 2
    client = Client(REMOTE_ADDR=ADDRESSES[0])
    for _ in range(2):
        log_in(client, 'LAWYER@example.org', 'a wrong passphrase')
    assert log_in(client, 'lawyer@example.org').status_code == 429
    assert '_auth_user_id' not in client.session
    # Other accounts from the same connection are not affected.
    assert log_in(client, 'applicant@example.org').status_code == 302


def test_failed_logins_elsewhere_do_not_lock_a_lawyer_out(lawyer, settings):
    settings.RATE_LIMIT_FAILED_LOGINS_FOR_ACCOUNT = 2
    for _ in range(5):
        log_in(Client(REMOTE_ADDR=ADDRESSES[0]), 'lawyer@example.org', 'a wrong passphrase')
    assert log_in(Client(REMOTE_ADDR=ADDRESSES[1]), 'lawyer@example.org').status_code == 302


def test_a_login_lasts_at_most_a_day(client, lawyer):
    log_in(client, 'lawyer@example.org')
    assert client.session.get_expiry_age() == 24 * 60 * 60


def test_right_logins_do_not_count(lawyer, settings):
    settings.RATE_LIMIT_FAILED_LOGINS = 1
    settings.RATE_LIMIT_FAILED_LOGINS_FOR_ACCOUNT = 1
    for _ in range(3):
        client = Client(REMOTE_ADDR=ADDRESSES[0])
        assert log_in(client, 'lawyer@example.org').status_code == 302


def test_the_address_and_email_are_kept_only_as_keyed_hashes(lawyer):
    log_in(Client(REMOTE_ADDR=ADDRESSES[0]), 'lawyer@example.org', 'a wrong passphrase')
    keys = list(cache._cache)
    assert keys
    assert not any(ADDRESSES[0] in key or 'lawyer@example.org' in key for key in keys)
    connection.ensure_connection()
    assert ADDRESSES[0] not in '\n'.join(connection.connection.iterdump())


def test_an_outside_next_address_is_not_followed(client, lawyer):
    response = client.post(LOGIN_URL, {
        'username': 'lawyer@example.org', 'password': PASSWORD, 'next': 'https://example.com/',
    })
    assert response['Location'] == reverse('accounts:home')
