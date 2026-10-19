import datetime

import pytest
from django.contrib.auth import authenticate, get_user_model
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.contrib.sessions.models import Session
from django.db import IntegrityError, transaction
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.models import LawyerProfile
from audit.models import Action, AuditEntry
from cases.models import Case
from tests.conftest import log_in_with_second_factor, make_case

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


def revoke(admin_client, profile, email='lawyer@example.org'):
    return admin_client.post(reverse('manage:revoke', args=[profile.pk]), {'email': email})


def test_revocation_needs_the_address_typed_as_confirmation(admin_client, lawyer):
    assert admin_client.get(reverse('manage:revoke', args=[lawyer.pk])).status_code == 200
    assert revoke(admin_client, lawyer, 'someone@example.org').status_code == 400
    assert admin_client.post(reverse('manage:revoke', args=[lawyer.pk])).status_code == 400
    lawyer.refresh_from_db()
    assert lawyer.status == Status.LAWYER and not AuditEntry.objects.exists()


def test_revocation_closes_all_the_lawyer_s_cases_and_no_one_else_s(admin_client, lawyer):
    open_cases = [make_case(lawyer, f'Case {n}') for n in range(2)]
    earlier = timezone.now()
    already_closed = make_case(lawyer, 'Old case')
    Case.objects.filter(pk=already_closed.pk).close(Case.ClosedBecause.LAWYER, now=earlier)
    other_user = User.objects.create_user('other@example.org')
    other = LawyerProfile.objects.create(user=other_user, bar='Lyon')
    other.verified_via, other.vouched_by = 'The bar directory', 'A defence organisation'
    other.save()
    other.change_status(Status.LAWYER)
    others_case = make_case(other, 'Not theirs')

    revoke(admin_client, lawyer)
    for case in open_cases:
        case.refresh_from_db()
        assert case.closed_at is not None and case.closed_because == Case.ClosedBecause.REVOCATION
    already_closed.refresh_from_db()
    assert already_closed.closed_at == earlier and already_closed.closed_because == Case.ClosedBecause.LAWYER
    others_case.refresh_from_db()
    assert others_case.closed_at is None
    assert Case.objects.count() == 4


def test_a_revoked_lawyer_sees_nothing_and_cannot_log_in(admin_client, lawyer):
    StaticDevice.objects.create(user=lawyer.user, name='recovery codes')
    lawyer_client = log_in_with_second_factor(Client(), lawyer.user)
    revoke(admin_client, lawyer)
    assert lawyer_client.get(reverse('lawyer:home')).status_code == 302
    assert authenticate(username='lawyer@example.org', password=PASSWORD) is None
    assert not TOTPDevice.objects.filter(user=lawyer.user).exists()
    assert not StaticDevice.objects.filter(user=lawyer.user).exists()
    sessions = [session.get_decoded().get('_auth_user_id') for session in Session.objects.all()]
    assert str(lawyer.user_id) not in sessions


def test_revocation_cannot_be_undone(admin_client, lawyer):
    revoke(admin_client, lawyer)
    assert admin_client.post(reverse('manage:reinstate', args=[lawyer.pk])).status_code == 404
    assert revoke(admin_client, lawyer).status_code == 404
    with pytest.raises(ValueError):
        LawyerProfile.objects.get(pk=lawyer.pk).change_status(Status.LAWYER)
    lawyer.refresh_from_db()
    assert lawyer.status == Status.REVOKED


def test_revocation_deletes_nothing_and_keeps_the_address_apart_from_the_login(admin_client, lawyer):
    make_case(lawyer, 'A case')
    revoke(admin_client, lawyer)
    lawyer.refresh_from_db()
    user = lawyer.user
    assert (user.email, user.former_email) == (None, 'lawyer@example.org')
    assert (user.first_name, lawyer.bar, lawyer.vouched_by) == ('Camille', 'Paris', 'A defence organisation')
    assert Case.objects.filter(lawyer_profile=lawyer).exists()
    entry = AuditEntry.objects.get()
    assert entry.action == Action.REVOCATION and entry.lawyer_profile == lawyer


def test_a_suspended_lawyer_can_be_revoked_and_their_cases_closed(admin_client, lawyer):
    case = make_case(lawyer, 'A case')
    admin_client.post(reverse('manage:suspend', args=[lawyer.pk]))
    revoke(admin_client, lawyer)
    lawyer.refresh_from_db()
    case.refresh_from_db()
    assert lawyer.status == Status.REVOKED and case.closed_because == Case.ClosedBecause.REVOCATION


def test_the_confirmation_ignores_case_and_spaces(admin_client, lawyer):
    assert revoke(admin_client, lawyer, '  Lawyer@Example.ORG ').status_code == 302


def test_with_no_open_cases_the_page_says_so(admin_client, lawyer):
    page = admin_client.get(reverse('manage:revoke', args=[lawyer.pk])).content.decode()
    assert "aucun dossier de recherche ouvert" in page


def test_an_applicant_cannot_be_revoked(admin_client):
    applicant = LawyerProfile.objects.create(user=User.objects.create_user('applicant@example.org'), bar='Paris')
    assert revoke(admin_client, applicant, 'applicant@example.org').status_code == 404


def test_confirming_one_revoked_address_does_not_confirm_another(admin_client, lawyer):
    revoke(admin_client, lawyer)
    other_user = User.objects.create_user('other@example.org', PASSWORD)
    other = LawyerProfile.objects.create(user=other_user, bar='Lyon')
    other.verified_via, other.vouched_by = 'The bar directory', 'A defence organisation'
    other.save()
    other.change_status(Status.LAWYER)
    revoke(admin_client, other, 'other@example.org')
    invitation = {'email': 'other@example.org', 'first_name': 'A', 'last_name': 'B', 'bar': 'Paris',
                  'invite_former_address': 'lawyer@example.org'}
    assert admin_client.post(reverse('manage:invite'), invitation).status_code == 200
    assert not mail.outbox


def test_inviting_a_revoked_lawyer_s_address_again_needs_a_second_look(admin_client, lawyer):
    revoke(admin_client, lawyer)
    invitation = {'email': 'lawyer@example.org', 'first_name': 'Camille', 'last_name': 'Exemple', 'bar': 'Paris'}
    response = admin_client.post(reverse('manage:invite'), invitation)
    assert response.status_code == 200 and not mail.outbox
    assert 'révoqué' in response.content.decode()
    assert 'type="checkbox"' in response.content.decode()
    response = admin_client.post(reverse('manage:invite'), {**invitation, 'invite_former_address': 'lawyer@example.org'})
    assert response.status_code == 302
    new = User.objects.get(email='lawyer@example.org')
    assert new.pk != lawyer.user_id and new.lawyer_profile.status == Status.APPLICANT
    lawyer.refresh_from_db()
    assert lawyer.status == Status.REVOKED


def test_cases_are_never_deleted(lawyer):
    case = make_case(lawyer, 'A case')
    with pytest.raises(ValueError):
        case.delete()
    with pytest.raises(ValueError):
        Case.objects.all().delete()


def test_a_lawyer_sees_their_open_cases_only(lawyer):
    make_case(lawyer, 'Bastille, 15 June')
    make_case(lawyer, 'Closed one').__class__.objects.filter(label='Closed one').close(
        Case.ClosedBecause.LAWYER,
    )
    other = LawyerProfile.objects.create(user=User.objects.create_user('other@example.org'), bar='Lyon')
    other.verified_via, other.vouched_by = 'The bar directory', 'A defence organisation'
    other.save()
    other.change_status(Status.LAWYER)
    make_case(other, 'Not theirs')
    page = log_in_with_second_factor(Client(), lawyer.user).get(reverse('lawyer:home')).content.decode()
    assert 'Bastille, 15 June' in page and 'Closed one' not in page and 'Not theirs' not in page


def test_a_closed_case_never_reopens_or_changes(lawyer):
    case = make_case(lawyer, 'A case')
    Case.objects.filter(pk=case.pk).close(Case.ClosedBecause.LAWYER)
    case.refresh_from_db()
    case.closed_at, case.closed_because = None, ''
    with pytest.raises(ValueError):
        case.save()
    with pytest.raises(ValueError):
        Case.objects.filter(pk=case.pk).update(closed_at=None, closed_because='')
    assert Case.objects.get(pk=case.pk).closed_at is not None


def test_only_a_lawyer_gets_a_new_case(admin_client, lawyer):
    revoke(admin_client, lawyer)
    with pytest.raises(ValueError):
        make_case(lawyer, 'Too late')


@pytest.mark.parametrize('fields', [
    {'closed_at': timezone.now(), 'closed_because': ''},
    {'closed_because': 'lawyer'},
    {'closed_at': timezone.now(), 'closed_because': 'bored'},
])
def test_the_database_checks_how_a_case_is_closed(lawyer, fields):
    with pytest.raises(IntegrityError), transaction.atomic():
        window = {'starts_at': timezone.now() - datetime.timedelta(hours=1), 'ends_at': timezone.now()}
        # Past create_with_area's own checks, to reach the database's.
        Case(lawyer_profile=lawyer, label='A case', margin_minutes=0, margin_rings=0, **window, **fields).save(_checked=True)


def test_revocation_deletes_the_reason_of_a_waiting_request(admin_client, lawyer):
    starts_at = timezone.now() - datetime.timedelta(days=1)
    case = make_case(lawyer, starts_at=starts_at, ends_at=starts_at + datetime.timedelta(hours=3), approval_reason='Two charges.')
    revoke(admin_client, lawyer)
    case.refresh_from_db()
    assert case.closed_because == Case.ClosedBecause.REVOCATION and case.approval.reason == ''
