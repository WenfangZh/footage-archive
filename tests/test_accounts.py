import pytest
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from accounts.models import LawyerProfile

pytestmark = pytest.mark.django_db

User = get_user_model()
Status = LawyerProfile.Status
PASSWORD = 'a long test passphrase'


def make_profile(email='applicant@example.org'):
    user = User.objects.create_user(email, PASSWORD)
    return LawyerProfile.objects.create(user=user, bar='Paris')


def admit(profile):
    profile.verified_via = 'The bar directory'
    profile.vouched_by = 'A defence organisation'
    profile.save()
    profile.change_status(Status.LAWYER)
    return profile


def stored_status(profile):
    return LawyerProfile.objects.get(pk=profile.pk).status


def test_accounts_use_the_archive_user_model():
    assert User._meta.label == 'accounts.User'
    assert 'username' not in {field.name for field in User._meta.get_fields()}


def test_people_log_in_with_their_email_address():
    user = User.objects.create_user('someone@example.org', PASSWORD)
    assert authenticate(username='someone@example.org', password=PASSWORD) == user
    assert authenticate(username='someone@example.org', password='a wrong passphrase') is None


def test_the_login_form_finds_the_account_by_email_address():
    user = User.objects.create_user('someone@example.org', PASSWORD)
    form = AuthenticationForm(data={'username': 'Someone@Example.org', 'password': PASSWORD})
    assert form.is_valid()
    assert form.get_user() == user


def test_the_email_address_is_matched_in_any_case():
    user = User.objects.create_user('Someone@Example.org', PASSWORD)
    assert user.email == 'someone@example.org'
    assert authenticate(username='SOMEONE@example.ORG', password=PASSWORD) == user


def test_a_changed_email_address_is_stored_in_lower_case():
    user = User.objects.create_user('someone@example.org', PASSWORD)
    user.email = 'New@Example.ORG'
    user.save()
    user.refresh_from_db()
    assert user.email == 'new@example.org'


def test_an_email_address_in_mixed_case_is_refused_by_the_database():
    user = User.objects.create_user('someone@example.org', PASSWORD)
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=user.pk).update(email='Someone@Example.org')


def test_look_alike_characters_are_normalised():
    # A fullwidth 'ｅ', as Django's login form would turn into a plain 'e'.
    user = User.objects.create_user('someone@ｅxample.org', PASSWORD)
    assert user.email == 'someone@example.org'


def test_an_email_address_belongs_to_one_account():
    User.objects.create_user('someone@example.org', PASSWORD)
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user('SOMEONE@example.org', PASSWORD)


def test_an_account_needs_an_email_address():
    with pytest.raises(ValueError):
        User.objects.create_user('', PASSWORD)


def test_an_account_made_without_a_password_cannot_log_in():
    user = User.objects.create_user('someone@example.org')
    assert not user.has_usable_password()
    assert authenticate(username='someone@example.org', password='') is None


def test_createsuperuser_makes_the_admin_account():
    call_command('createsuperuser', interactive=False, email='admin@example.org', verbosity=0)
    admin = User.objects.get(email='admin@example.org')
    assert admin.is_admin and admin.is_staff and admin.is_superuser


def test_createsuperuser_refuses_an_address_already_used_in_another_case():
    User.objects.create_user('someone@example.org', PASSWORD)
    with pytest.raises(CommandError):
        call_command('createsuperuser', interactive=False, email='Someone@Example.org', verbosity=0)


@pytest.mark.parametrize('flags', [{'is_staff': True}, {'is_superuser': True}])
def test_staff_and_superuser_go_together(flags):
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user('someone@example.org', PASSWORD, **flags)


def test_the_database_knows_every_status_and_no_other():
    constraint = next(c for c in LawyerProfile._meta.constraints if c.name == 'known_status')
    assert dict(constraint.condition.children)['status__in'] == Status.values


def test_a_lawyer_profile_starts_as_an_applicant():
    assert make_profile().status == Status.APPLICANT
    user = User.objects.create_user('lawyer@example.org', PASSWORD)
    with pytest.raises(ValueError):
        LawyerProfile.objects.create(user=user, bar='Paris', status=Status.LAWYER)


@pytest.mark.parametrize('missing', ['verified_via', 'vouched_by'])
def test_admission_needs_verification_and_a_vouch(missing):
    profile = make_profile()
    profile.verified_via = 'The bar directory'
    profile.vouched_by = 'A defence organisation'
    setattr(profile, missing, '')
    profile.save()
    with pytest.raises(IntegrityError), transaction.atomic():
        profile.change_status(Status.LAWYER)
    assert stored_status(profile) == Status.APPLICANT


@pytest.mark.parametrize('statuses', [
    [Status.LAWYER],
    [Status.DECLINED],
    [Status.LAWYER, Status.SUSPENDED, Status.LAWYER],
    [Status.LAWYER, Status.REVOKED],
    [Status.LAWYER, Status.SUSPENDED, Status.REVOKED],
])
def test_allowed_status_changes(statuses):
    profile = make_profile()
    profile.verified_via = 'The bar directory'
    profile.vouched_by = 'A defence organisation'
    profile.save()
    for status in statuses:
        profile.change_status(status)
    assert profile.status == statuses[-1]
    assert stored_status(profile) == statuses[-1]


@pytest.mark.parametrize('before, after', [
    ([], Status.SUSPENDED),
    ([], Status.REVOKED),
    ([Status.LAWYER], Status.APPLICANT),
    ([Status.LAWYER], Status.DECLINED),
    ([Status.LAWYER, Status.SUSPENDED], Status.APPLICANT),
])
def test_forbidden_status_changes(before, after):
    profile = admit(make_profile()) if before else make_profile()
    for status in before[1:]:
        profile.change_status(status)
    with pytest.raises(ValueError):
        profile.change_status(after)
    assert stored_status(profile) == (before[-1] if before else Status.APPLICANT)


@pytest.mark.parametrize('after', [Status.APPLICANT, Status.LAWYER, Status.SUSPENDED, Status.DECLINED])
def test_revocation_cannot_be_undone(after):
    profile = admit(make_profile())
    profile.change_status(Status.REVOKED)
    with pytest.raises(ValueError):
        profile.change_status(after)
    assert stored_status(profile) == Status.REVOKED


@pytest.mark.parametrize('after', [Status.APPLICANT, Status.LAWYER])
def test_declining_cannot_be_undone(after):
    profile = make_profile()
    profile.change_status(Status.DECLINED)
    with pytest.raises(ValueError):
        profile.change_status(after)
    assert stored_status(profile) == Status.DECLINED


@pytest.mark.parametrize('final', [Status.REVOKED, Status.DECLINED])
def test_a_revoked_or_declined_profile_cannot_be_changed_at_all(final):
    profile = make_profile()
    if final == Status.REVOKED:
        admit(profile)
    profile.change_status(final)
    profile.bar = 'Lyon'
    with pytest.raises(ValueError):
        profile.save()


def test_saving_does_not_change_the_status():
    profile = admit(make_profile())
    profile.status = Status.SUSPENDED
    with pytest.raises(ValueError):
        profile.save()
    assert stored_status(profile) == Status.LAWYER


def test_an_out_of_date_copy_cannot_undo_a_suspension():
    profile = admit(make_profile())
    old_copy = LawyerProfile.objects.get(pk=profile.pk)
    profile.change_status(Status.SUSPENDED)
    old_copy.bar = 'Lyon'
    with pytest.raises(ValueError):
        old_copy.save()
    assert stored_status(profile) == Status.SUSPENDED


def test_an_out_of_date_copy_cannot_undo_a_revocation_through_change_status():
    profile = admit(make_profile())
    old_copy = LawyerProfile.objects.get(pk=profile.pk)
    profile.change_status(Status.REVOKED)
    with pytest.raises(ValueError):
        old_copy.change_status(Status.SUSPENDED)
    assert stored_status(profile) == Status.REVOKED


def test_an_applicant_verification_and_vouch_can_be_recorded_and_corrected():
    profile = make_profile()
    profile.verified_via = 'The bar directory'
    profile.save()
    profile.verified_via = 'The bar directory, by phone'
    profile.vouched_by = 'A defence organisation'
    profile.save()
    assert LawyerProfile.objects.get(pk=profile.pk).verified_via == 'The bar directory, by phone'


@pytest.mark.parametrize('field', LawyerProfile.ADMISSION_FIELDS)
def test_how_a_lawyer_was_admitted_cannot_be_changed(field):
    profile = admit(make_profile())
    setattr(profile, field, 'Someone else')
    with pytest.raises(ValueError):
        profile.save()


def test_a_profile_cannot_move_to_another_account():
    profile = admit(make_profile())
    profile.user = User.objects.create_user('someone@example.org', PASSWORD)
    with pytest.raises(ValueError):
        profile.save()


def test_a_profile_cannot_be_deleted():
    profile = make_profile()
    with pytest.raises(ValueError):
        profile.delete()
    with pytest.raises(ValueError):
        LawyerProfile.objects.filter(pk=profile.pk).delete()
    assert LawyerProfile.objects.filter(pk=profile.pk).exists()


def test_the_admin_account_cannot_have_a_lawyer_profile():
    admin = User.objects.create_superuser('admin@example.org', PASSWORD)
    with pytest.raises(ValueError):
        LawyerProfile.objects.create(user=admin, bar='Paris')


def test_an_applicant_account_cannot_become_the_admin_account():
    user = make_profile().user
    user.is_staff = user.is_superuser = True
    with pytest.raises(ValueError):
        user.save()


def test_an_account_with_a_lawyer_profile_cannot_be_deleted():
    user = make_profile().user
    with pytest.raises(ProtectedError):
        user.delete()


def test_a_new_profile_with_an_existing_id_cannot_replace_a_revoked_one():
    profile = admit(make_profile())
    profile.change_status(Status.REVOKED)
    other = User.objects.create_user('someone@example.org', PASSWORD)
    with pytest.raises(IntegrityError), transaction.atomic():
        LawyerProfile(pk=profile.pk, user=other, bar='Lyon').save()
    stored = LawyerProfile.objects.get(pk=profile.pk)
    assert stored.status == Status.REVOKED and stored.user == profile.user


def test_saving_never_writes_the_status(monkeypatch):
    # As if a suspension committed between save()'s check and its write.
    profile = admit(make_profile())
    LawyerProfile.objects.filter(pk=profile.pk).update(status=Status.SUSPENDED)
    monkeypatch.setattr(LawyerProfile, '_refuse_forbidden_changes', lambda self: None)
    profile.bar = 'Lyon'
    profile.save()
    stored = LawyerProfile.objects.get(pk=profile.pk)
    assert stored.status == Status.SUSPENDED and stored.bar == 'Lyon'


def test_profiles_cannot_be_created_or_changed_in_bulk():
    profile = make_profile()
    user = User.objects.create_user('someone@example.org', PASSWORD)
    with pytest.raises(ValueError):
        LawyerProfile.objects.bulk_create([LawyerProfile(user=user, bar='Paris')])
    profile.bar = 'Lyon'
    with pytest.raises(ValueError):
        LawyerProfile.objects.bulk_update([profile], ['bar'])


def test_accounts_closed_for_good_can_free_their_address():
    first = User.objects.create_user('someone@example.org', PASSWORD)
    second = User.objects.create_user('other@example.org', PASSWORD)
    for user in (first, second):
        user.email = ''
        user.save()
    assert User.objects.filter(email__isnull=True).count() == 2
    assert authenticate(username='', password=PASSWORD) is None
    # The address can now belong to a new account.
    assert User.objects.create_user('someone@example.org', PASSWORD) != first


def test_a_former_address_does_not_log_in():
    user = User.objects.create_user('someone@example.org', PASSWORD)
    user.former_email, user.email = user.email, ''
    user.save()
    assert authenticate(username='someone@example.org', password=PASSWORD) is None
    # A new account can take the address while the old one keeps it as its former address.
    assert User.objects.create_user('someone@example.org', PASSWORD).pk != user.pk
