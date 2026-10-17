import pytest
from django.contrib.auth import authenticate, get_user_model
from django.test import Client
from django.urls import reverse
from unittest import mock

from accounts.models import LawyerProfile
from audit.models import Action, AuditEntry
from tests.conftest import log_in_with_second_factor

pytestmark = pytest.mark.django_db

User = get_user_model()
Status = LawyerProfile.Status
PASSWORD = 'a long test passphrase'


@pytest.fixture
def admin_client():
    return log_in_with_second_factor(Client(), User.objects.create_superuser('admin@example.org'))


@pytest.fixture
def lawyer():
    user = User.objects.create_user('lawyer@example.org', PASSWORD, first_name='Camille', last_name='Exemple')
    profile = LawyerProfile.objects.create(user=user, bar='Paris')
    profile.verified_via, profile.vouched_by = 'The bar directory', 'A defence organisation'
    profile.save()
    profile.change_status(Status.LAWYER)
    return profile


def test_a_suspended_lawyer_sees_nothing_at_once_and_a_reinstated_one_sees_again(admin_client, lawyer):
    lawyer_client = log_in_with_second_factor(Client(), lawyer.user)
    assert lawyer_client.get(reverse('lawyer:home')).status_code == 200

    assert admin_client.post(reverse('manage:suspend', args=[lawyer.pk])).status_code == 302
    lawyer.refresh_from_db()
    assert lawyer.status == Status.SUSPENDED and not lawyer.user.is_active
    # The open session ends at once, and the password no longer logs in.
    assert lawyer_client.get(reverse('lawyer:home')).status_code == 302
    assert authenticate(username='lawyer@example.org', password=PASSWORD) is None

    admin_client.post(reverse('manage:reinstate', args=[lawyer.pk]))
    lawyer.refresh_from_db()
    assert lawyer.status == Status.LAWYER and lawyer.user.is_active
    # The session open before the suspension does not come back: a new login is needed.
    assert lawyer_client.get(reverse('lawyer:home')).status_code == 302
    assert log_in_with_second_factor(Client(), lawyer.user).get(reverse('lawyer:home')).status_code == 200


def test_suspension_and_reinstatement_write_audit_entries(admin_client, lawyer):
    admin_client.post(reverse('manage:suspend', args=[lawyer.pk]))
    admin_client.post(reverse('manage:reinstate', args=[lawyer.pk]))
    assert list(AuditEntry.objects.order_by('pk').values_list('action', flat=True)) == [
        Action.SUSPENSION, Action.REINSTATEMENT,
    ]
    assert all(entry.lawyer_profile == lawyer and entry.admin.is_admin for entry in AuditEntry.objects.all())


def test_suspension_deletes_nothing(admin_client, lawyer):
    admin_client.post(reverse('manage:suspend', args=[lawyer.pk]))
    lawyer.refresh_from_db()
    user = lawyer.user
    assert (user.email, user.first_name, lawyer.bar, lawyer.vouched_by) == (
        'lawyer@example.org', 'Camille', 'Paris', 'A defence organisation',
    )


@pytest.mark.parametrize('action, status', [
    ('manage:suspend', Status.SUSPENDED), ('manage:reinstate', Status.LAWYER),
])
def test_each_works_only_from_the_status_before_it(admin_client, lawyer, action, status):
    if status == Status.LAWYER:
        assert admin_client.post(reverse(action, args=[lawyer.pk])).status_code == 404
    else:
        admin_client.post(reverse(action, args=[lawyer.pk]))
        assert admin_client.post(reverse(action, args=[lawyer.pk])).status_code == 404
    assert AuditEntry.objects.count() == (1 if status == Status.SUSPENDED else 0)


def test_an_applicant_cannot_be_suspended(admin_client):
    user = User.objects.create_user('applicant@example.org')
    applicant = LawyerProfile.objects.create(user=user, bar='Paris')
    assert admin_client.post(reverse('manage:suspend', args=[applicant.pk])).status_code == 404
    user.refresh_from_db()
    assert user.is_active and not AuditEntry.objects.exists()


@pytest.mark.parametrize('final', [Status.REVOKED, Status.DECLINED])
def test_reinstating_never_reopens_a_revoked_or_declined_account(admin_client, final):
    user = User.objects.create_user(f'{final}@example.org', PASSWORD)
    profile = LawyerProfile.objects.create(user=user, bar='Paris')
    if final == Status.REVOKED:
        profile.verified_via, profile.vouched_by = 'The bar directory', 'A defence organisation'
        profile.save()
        profile.change_status(Status.LAWYER)
    profile.change_status(final)
    user.is_active = False
    user.save()
    assert admin_client.post(reverse('manage:reinstate', args=[profile.pk])).status_code == 404
    user.refresh_from_db()
    assert not user.is_active


def test_if_the_audit_entry_fails_nothing_changes(admin_client, lawyer):
    with mock.patch('accounts.access.record', side_effect=RuntimeError('audit failed')):
        with pytest.raises(RuntimeError):
            admin_client.post(reverse('manage:suspend', args=[lawyer.pk]))
    lawyer.refresh_from_db()
    assert lawyer.status == Status.LAWYER and lawyer.user.is_active


def test_a_round_trip_changes_nothing_else(admin_client, lawyer):
    admin_client.post(reverse('manage:suspend', args=[lawyer.pk]))
    admin_client.post(reverse('manage:reinstate', args=[lawyer.pk]))
    lawyer.refresh_from_db()
    user = lawyer.user
    assert (user.email, user.first_name, lawyer.bar, lawyer.verified_via, lawyer.vouched_by) == (
        'lawyer@example.org', 'Camille', 'Paris', 'The bar directory', 'A defence organisation',
    )
    assert user.check_password(PASSWORD)


@pytest.mark.parametrize('action', ['manage:suspend', 'manage:reinstate'])
def test_suspending_and_reinstating_need_a_post(admin_client, lawyer, action):
    assert admin_client.get(reverse(action, args=[lawyer.pk])).status_code == 405
    lawyer.refresh_from_db()
    assert lawyer.status == Status.LAWYER
