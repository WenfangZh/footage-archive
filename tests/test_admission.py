import pytest
from django.contrib.auth import authenticate, get_user_model
from django.test import Client
from django.urls import reverse
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice
from unittest import mock

from accounts.models import LawyerProfile
from audit.models import Action, AuditEntry
from tests.conftest import log_in_with_second_factor

pytestmark = pytest.mark.django_db

User = get_user_model()
Status = LawyerProfile.Status
PASSWORD = 'a long test passphrase'
ADMISSION = {'verified_via': 'The bar directory, by phone', 'vouched_by': 'A defence organisation'}


@pytest.fixture
def admin_client(client):
    return log_in_with_second_factor(client, User.objects.create_superuser('admin@example.org'))


@pytest.fixture
def applicant():
    # Has accepted the invitation: a password and a second factor.
    user = User.objects.create_user('applicant@example.org', PASSWORD, first_name='Camille', last_name='Exemple')
    TOTPDevice.objects.create(user=user, name='authenticator app', confirmed=True)
    StaticDevice.objects.create(user=user, name='recovery codes')
    return LawyerProfile.objects.create(user=user, bar='Paris')


def test_admitting_records_how_and_makes_a_lawyer_who_sees_the_lawyer_pages(admin_client, applicant):
    response = admin_client.post(reverse('manage:applicant', args=[applicant.pk]), ADMISSION)
    assert response.status_code == 302
    applicant.refresh_from_db()
    assert applicant.status == Status.LAWYER
    assert applicant.verified_via == ADMISSION['verified_via'] and applicant.vouched_by == ADMISSION['vouched_by']
    entry = AuditEntry.objects.get()
    assert entry.action == Action.ADMISSION and entry.lawyer_profile == applicant
    lawyer_client = log_in_with_second_factor(Client(), applicant.user)
    assert lawyer_client.get(reverse('lawyer:home')).status_code == 200


@pytest.mark.parametrize('missing', ['verified_via', 'vouched_by'])
def test_admission_needs_both_the_verification_and_the_vouch(admin_client, applicant, missing):
    response = admin_client.post(reverse('manage:applicant', args=[applicant.pk]), {**ADMISSION, missing: ''})
    assert response.status_code == 200
    applicant.refresh_from_db()
    assert applicant.status == Status.APPLICANT
    assert not AuditEntry.objects.exists()


def test_only_applicants_can_be_admitted_or_declined(admin_client, applicant):
    applicant.change_status(Status.DECLINED)
    assert admin_client.post(reverse('manage:applicant', args=[applicant.pk]), ADMISSION).status_code == 404
    assert admin_client.post(reverse('manage:decline', args=[applicant.pk]), {'confirm': 'yes'}).status_code == 404


def test_declining_needs_a_confirmation(admin_client, applicant):
    assert admin_client.post(reverse('manage:decline', args=[applicant.pk])).status_code == 400
    applicant.refresh_from_db()
    assert applicant.status == Status.APPLICANT


def test_declining_erases_the_name_bar_and_address_and_ends_access(admin_client, applicant):
    applicant_client = log_in_with_second_factor(Client(), applicant.user)
    admin_client.post(reverse('manage:decline', args=[applicant.pk]), {'confirm': 'yes'})
    applicant.refresh_from_db()
    user = applicant.user
    assert applicant.status == Status.DECLINED and applicant.bar == ''
    assert (user.first_name, user.last_name, user.email, user.former_email) == ('', '', None, '')
    assert not user.is_active and not user.has_usable_password()
    assert not TOTPDevice.objects.filter(user=user).exists()
    assert authenticate(username='applicant@example.org', password=PASSWORD) is None
    # An open session ends at once.
    assert applicant_client.get(reverse('accounts:home')).status_code == 302
    entry = AuditEntry.objects.get()
    assert entry.action == Action.DECLINING and entry.lawyer_profile == applicant


def test_a_declined_applicant_can_be_invited_again_as_a_new_applicant(admin_client, applicant):
    admin_client.post(reverse('manage:decline', args=[applicant.pk]), {'confirm': 'yes'})
    admin_client.post(reverse('manage:invite'), {
        'email': 'applicant@example.org', 'first_name': 'Camille', 'last_name': 'Exemple', 'bar': 'Lyon',
    })
    new = User.objects.get(email='applicant@example.org')
    assert new.pk != applicant.user_id
    assert new.lawyer_profile.status == Status.APPLICANT
    applicant.refresh_from_db()
    assert applicant.status == Status.DECLINED


@pytest.mark.parametrize('missing', ['password', 'second factor'])
def test_nobody_is_admitted_before_setting_a_password_and_a_second_factor(admin_client, applicant, missing):
    user = applicant.user
    if missing == 'password':
        user.set_unusable_password()
        user.save()
    else:
        TOTPDevice.objects.filter(user=user).delete()
    response = admin_client.post(reverse('manage:applicant', args=[applicant.pk]), ADMISSION)
    assert response.status_code == 200
    applicant.refresh_from_db()
    assert applicant.status == Status.APPLICANT and applicant.verified_via == ''
    assert not AuditEntry.objects.exists()


def test_opening_the_decline_page_changes_nothing(admin_client, applicant):
    assert admin_client.get(reverse('manage:decline', args=[applicant.pk]) + '?confirm=yes').status_code == 200
    applicant.refresh_from_db()
    assert applicant.status == Status.APPLICANT and applicant.user.is_active


def test_declining_removes_recovery_codes_too(admin_client, applicant):
    admin_client.post(reverse('manage:decline', args=[applicant.pk]), {'confirm': 'yes'})
    assert not StaticDevice.objects.filter(user=applicant.user).exists()


def test_if_declining_fails_halfway_nothing_changes(admin_client, applicant):
    with mock.patch('accounts.admission.record', side_effect=RuntimeError('audit failed')):
        with pytest.raises(RuntimeError):
            admin_client.post(reverse('manage:decline', args=[applicant.pk]), {'confirm': 'yes'})
    applicant.refresh_from_db()
    user = applicant.user
    assert applicant.status == Status.APPLICANT and applicant.bar == 'Paris'
    assert (user.email, user.first_name, user.is_active) == ('applicant@example.org', 'Camille', True)
    assert TOTPDevice.objects.filter(user=user).exists()


def test_a_lawyer_cannot_be_admitted_or_declined_again(admin_client, applicant):
    admin_client.post(reverse('manage:applicant', args=[applicant.pk]), ADMISSION)
    assert admin_client.post(reverse('manage:applicant', args=[applicant.pk]), ADMISSION).status_code == 404
    assert admin_client.post(reverse('manage:decline', args=[applicant.pk]), {'confirm': 'yes'}).status_code == 404
    assert AuditEntry.objects.count() == 1
