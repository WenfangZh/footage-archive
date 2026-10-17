import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django_otp.oath import TOTP
from django_otp.plugins.otp_static.models import StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.models import LawyerProfile

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = 'a long test passphrase'
SETUP_URL = reverse('accounts:two_factor_setup')
VERIFY_URL = reverse('accounts:two_factor_verify')


@pytest.fixture
def lawyer():
    user = User.objects.create_user('lawyer@example.org', PASSWORD)
    profile = LawyerProfile.objects.create(user=user, bar='Paris')
    profile.verified_via = 'The bar directory'
    profile.vouched_by = 'A defence organisation'
    profile.save()
    profile.change_status(LawyerProfile.Status.LAWYER)
    return user


def code_for(device):
    # What the authenticator app shows now.
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    return f'{totp.token():0{device.digits}d}'


def set_up(client):
    client.get(SETUP_URL)
    device = TOTPDevice.objects.get(confirmed=False)
    code = code_for(device)
    return device, code, client.post(SETUP_URL, {'code': code})


def test_setting_up_shows_a_qr_code_and_the_key(client, lawyer):
    client.force_login(lawyer)
    page = client.get(SETUP_URL).content.decode()
    assert '<svg' in page
    device = TOTPDevice.objects.get(user=lawyer, confirmed=False)
    assert re.search(r'<code>([A-Z2-7]{4} ?)+</code>', page)
    assert 'Footage%20archive' in device.config_url


def test_a_right_code_finishes_setting_up_and_shows_recovery_codes_once(client, lawyer):
    client.force_login(lawyer)
    device, _, response = set_up(client)
    device.refresh_from_db()
    assert device.confirmed
    assert [template.name for template in response.templates][0] == 'accounts/recovery_codes.html'
    codes = response.context['codes']
    assert len(codes) == 10 and len(set(codes)) == 10
    assert client.get(reverse('lawyer:home')).status_code == 200
    # Once set up, the setup page offers nothing: no new key, no codes again.
    assert client.get(SETUP_URL).status_code == 302
    assert TOTPDevice.objects.filter(user=lawyer).count() == 1


def test_a_wrong_code_does_not_finish_setting_up(client, lawyer):
    client.force_login(lawyer)
    client.get(SETUP_URL)
    response = client.post(SETUP_URL, {'code': '000000'})
    assert response.status_code == 200
    assert not TOTPDevice.objects.filter(user=lawyer, confirmed=True).exists()
    assert client.get(reverse('lawyer:home')).status_code == 302


def test_each_login_needs_a_code_from_the_app(client, lawyer):
    client.force_login(lawyer)
    _, used_code, _ = set_up(client)
    client.logout()
    client.force_login(lawyer)
    response = client.get(reverse('lawyer:home'))
    assert response['Location'].startswith(VERIFY_URL)
    # The code just used for setting up cannot be used again.
    assert client.post(VERIFY_URL, {'code': used_code}).status_code == 200
    assert client.get(reverse('lawyer:home')).status_code == 302


def test_a_code_from_the_app_lets_the_lawyer_in(client, lawyer):
    device = TOTPDevice.objects.create(user=lawyer, name='authenticator app', confirmed=True)
    client.force_login(lawyer)
    response = client.post(VERIFY_URL, {'code': code_for(device), 'next': reverse('lawyer:home')})
    assert response['Location'] == reverse('lawyer:home')
    assert client.get(reverse('lawyer:home')).status_code == 200


def test_a_wrong_code_is_refused_and_counted(client, lawyer):
    device = TOTPDevice.objects.create(user=lawyer, name='authenticator app', confirmed=True)
    client.force_login(lawyer)
    assert client.post(VERIFY_URL, {'code': '000000'}).status_code == 200
    device.refresh_from_db()
    # django-otp makes each further attempt wait longer.
    assert device.throttling_failure_count == 1
    assert client.get(reverse('lawyer:home')).status_code == 302


def test_a_recovery_code_works_once(client, lawyer):
    client.force_login(lawyer)
    *_, response = set_up(client)
    code = response.context['codes'][0]
    for attempt in range(2):
        client.logout()
        client.force_login(lawyer)
        client.post(VERIFY_URL, {'code': code})
        expected = 200 if attempt == 0 else 302
        assert client.get(reverse('lawyer:home')).status_code == expected
    assert StaticToken.objects.count() == 9


def test_an_applicant_sets_up_the_second_factor_before_admission(client):
    applicant = User.objects.create_user('applicant@example.org', PASSWORD)
    LawyerProfile.objects.create(user=applicant, bar='Paris')
    client.force_login(applicant)
    assert client.get(reverse('accounts:home'))['Location'].startswith(SETUP_URL)


def test_the_admin_needs_the_second_factor_too(client):
    admin = User.objects.create_superuser('admin@example.org', PASSWORD)
    client.force_login(admin)
    assert client.get(reverse('manage:home'))['Location'].startswith(SETUP_URL)


def test_an_outside_next_address_is_not_followed(client, lawyer):
    device = TOTPDevice.objects.create(user=lawyer, name='authenticator app', confirmed=True)
    client.force_login(lawyer)
    response = client.post(VERIFY_URL, {'code': code_for(device), 'next': 'https://example.com/'})
    assert response['Location'] == reverse('accounts:home')


def test_no_page_turns_the_second_factor_off_or_replaces_it(client, lawyer):
    client.force_login(lawyer)
    *_, response = set_up(client)
    before = (
        set(TOTPDevice.objects.values_list('pk', 'confirmed', 'key')),
        set(StaticToken.objects.values_list('pk', 'token')),
    )
    for url in every_url():
        client.get(url)
        client.post(url, {'code': '000000'})
        client.force_login(lawyer)
    after = (
        set(TOTPDevice.objects.values_list('pk', 'confirmed', 'key')),
        set(StaticToken.objects.values_list('pk', 'token')),
    )
    assert after == before


def every_url():
    from django.urls import URLResolver, get_resolver
    urls = []

    def walk(patterns, namespace):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, ':'.join(filter(None, [namespace, pattern.namespace])))
            elif pattern.name and not pattern.pattern.converters:
                urls.append(reverse(':'.join(filter(None, [namespace, pattern.name]))))
    walk(get_resolver().url_patterns, '')
    return urls


def test_posting_to_setup_without_opening_it_starts_again(client, lawyer):
    client.force_login(lawyer)
    response = client.post(SETUP_URL, {'code': '123456'})
    assert response.status_code == 302 and response['Location'].startswith(SETUP_URL)


def test_posting_to_setup_once_set_up_changes_nothing(client, lawyer):
    client.force_login(lawyer)
    *_, first = set_up(client)
    response = client.post(SETUP_URL, {'code': '123456'})
    assert response.status_code == 302
    assert StaticToken.objects.count() == 10
    assert set(StaticToken.objects.values_list('token', flat=True)) == set(first.context['codes'])


def test_reloading_setup_keeps_the_key_just_scanned(client, lawyer):
    client.force_login(lawyer)
    client.get(SETUP_URL)
    first = TOTPDevice.objects.get(confirmed=False).key
    client.get(SETUP_URL)
    assert TOTPDevice.objects.get(confirmed=False).key == first


def test_a_recovery_code_is_accepted_in_capitals_and_with_spaces(client, lawyer):
    client.force_login(lawyer)
    *_, response = set_up(client)
    code = response.context['codes'][0]
    client.logout()
    client.force_login(lawyer)
    client.post(VERIFY_URL, {'code': f' {code[:4].upper()} {code[4:].upper()} '})
    assert client.get(reverse('lawyer:home')).status_code == 200


def test_a_wrong_recovery_code_does_not_slow_down_the_app(client, lawyer):
    device = TOTPDevice.objects.create(user=lawyer, name='authenticator app', confirmed=True)
    client.force_login(lawyer)
    client.post(VERIFY_URL, {'code': 'abcdefgh'})
    device.refresh_from_db()
    assert device.throttling_failure_count == 0


def test_while_slowed_down_a_right_code_waits_with_a_clear_message(client, lawyer):
    device = TOTPDevice.objects.create(user=lawyer, name='authenticator app', confirmed=True)
    client.force_login(lawyer)
    client.post(VERIFY_URL, {'code': '000000'})
    response = client.post(VERIFY_URL, {'code': code_for(device)})
    assert 'Trop de codes erronés' in response.content.decode()
    assert client.get(reverse('lawyer:home')).status_code == 302


@pytest.mark.parametrize('url', [SETUP_URL, VERIFY_URL])
def test_accounts_with_no_access_get_nothing_here(client, lawyer, url):
    LawyerProfile.objects.get(user=lawyer).change_status(LawyerProfile.Status.SUSPENDED)
    client.force_login(lawyer)
    assert client.get(url).status_code == 403
    assert not TOTPDevice.objects.exists()


def test_the_next_address_survives_the_detour_whole(client, lawyer):
    TOTPDevice.objects.create(user=lawyer, name='authenticator app', confirmed=True)
    client.force_login(lawyer)
    response = client.get(reverse('lawyer:home') + '?a=1&b=2')
    assert response['Location'] == VERIFY_URL + '?next=%2Flawyer%2F%3Fa%3D1%26b%3D2'
