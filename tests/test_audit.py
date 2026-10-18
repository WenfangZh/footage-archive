import datetime
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, models, transaction
from django.db.models import ProtectedError

from accounts.models import LawyerProfile
from audit.models import (
    ADMIN_ACTIONS, ADMIN_CASE_ACTIONS, CASE_ACTIONS, CLIP_ACTIONS, Action, AppendOnly, AuditEntry, record,
)
from clips.editing import delete_clip
from clips.models import Clip
from tests.conftest import make_case

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def admin():
    return User.objects.create_superuser('admin@example.org')


@pytest.fixture
def applicant():
    user = User.objects.create_user('applicant@example.org')
    return LawyerProfile.objects.create(user=user, bar='Paris')


@pytest.fixture
def entry(admin, applicant):
    return record(Action.INVITATION, applicant, admin=admin)


def stored_action(entry):
    return AuditEntry.objects.get(pk=entry.pk).action


def test_an_entry_records_the_action_the_person_concerned_the_admin_and_the_time(entry, admin, applicant):
    stored = AuditEntry.objects.get(pk=entry.pk)
    assert stored.action == Action.INVITATION
    assert stored.lawyer_profile == applicant
    assert stored.admin == admin
    assert stored.at is not None


def test_the_time_is_kept_precisely(admin, applicant):
    # Unlike upload times (SPEC.md §6), not cut to a 4-hour block.
    now = datetime.datetime(2026, 6, 15, 21, 59, 59, tzinfo=datetime.UTC)
    with mock.patch('django.utils.timezone.now', return_value=now):
        entry = record(Action.ADMISSION, applicant, admin=admin)
    assert AuditEntry.objects.get(pk=entry.pk).at == now


def test_the_time_cannot_be_backdated(admin, applicant):
    entry = AuditEntry.objects.create(
        action=Action.INVITATION, lawyer_profile=applicant, admin=admin,
        at=datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC),
    )
    assert AuditEntry.objects.get(pk=entry.pk).at.year != 2000


@pytest.mark.parametrize('action', [action for action in ADMIN_ACTIONS if action not in ADMIN_CASE_ACTIONS])
def test_the_admin_actions_name_the_admin(action, applicant):
    with pytest.raises(IntegrityError), transaction.atomic():
        record(action, applicant)


def test_only_the_admin_account_can_be_the_admin_on_an_entry(applicant):
    someone = User.objects.create_user('someone@example.org')
    with pytest.raises(ValueError):
        record(Action.INVITATION, applicant, admin=someone)


def test_an_unknown_action_is_refused(admin, applicant):
    with pytest.raises(ValueError):
        record('suspention', applicant, admin=admin)
    assert not AuditEntry.objects.exists()


def test_an_entry_cannot_be_changed(entry):
    entry.action = Action.REVOCATION
    with pytest.raises(AppendOnly):
        entry.save()
    assert stored_action(entry) == Action.INVITATION


def test_a_new_entry_cannot_replace_an_old_one(entry, admin, applicant):
    with pytest.raises(IntegrityError), transaction.atomic():
        AuditEntry(pk=entry.pk, action=Action.REVOCATION, lawyer_profile=applicant, admin=admin).save()
    assert stored_action(entry) == Action.INVITATION


def test_entries_cannot_be_changed_in_bulk(entry):
    with pytest.raises(AppendOnly):
        AuditEntry.objects.update(action=Action.REVOCATION)
    entry.action = Action.REVOCATION
    with pytest.raises(AppendOnly):
        AuditEntry.objects.bulk_update([entry], ['action'])
    assert stored_action(entry) == Action.INVITATION


def test_entries_cannot_be_added_in_bulk_or_overwritten_that_way(entry, admin, applicant):
    replacement = AuditEntry(pk=entry.pk, action=Action.REVOCATION, lawyer_profile=applicant, admin=admin)
    with pytest.raises(AppendOnly):
        AuditEntry.objects.bulk_create(
            [replacement], update_conflicts=True, unique_fields=['id'], update_fields=['action'],
        )
    assert stored_action(entry) == Action.INVITATION


def test_the_manager_django_uses_internally_refuses_too(entry):
    with pytest.raises(AppendOnly):
        AuditEntry._base_manager.filter(pk=entry.pk).update(action=Action.REVOCATION)
    with pytest.raises(AppendOnly):
        AuditEntry._base_manager.filter(pk=entry.pk).delete()
    assert stored_action(entry) == Action.INVITATION


def test_an_entry_cannot_be_deleted(entry):
    with pytest.raises(AppendOnly):
        entry.delete()
    with pytest.raises(AppendOnly):
        AuditEntry.objects.all().delete()
    with pytest.raises(AppendOnly):
        AuditEntry.objects.filter(pk=entry.pk).delete()
    assert AuditEntry.objects.filter(pk=entry.pk).exists()


def test_the_people_an_entry_names_cannot_be_deleted(entry, admin, applicant):
    with pytest.raises(ProtectedError):
        admin.delete()
    with pytest.raises(ValueError):
        applicant.delete()
    with pytest.raises(ProtectedError):
        applicant.user.delete()
    assert AuditEntry.objects.filter(pk=entry.pk).exists()


def test_every_foreign_key_protects_the_entries():
    # CASCADE or SET_NULL would let Django delete or change entries when what they name goes.
    foreign_keys = [field for field in AuditEntry._meta.get_fields() if isinstance(field, models.ForeignKey)]
    assert foreign_keys
    assert all(field.remote_field.on_delete is models.PROTECT for field in foreign_keys)


def test_save_base_and_fixtures_cannot_change_an_entry(entry):
    entry.action = Action.REVOCATION
    with pytest.raises(AppendOnly), transaction.atomic():
        entry.save_base()
    assert stored_action(entry) == Action.INVITATION


def test_the_database_refuses_an_unknown_action(admin, applicant):
    with pytest.raises(IntegrityError), transaction.atomic():
        # save_base() skips save()'s own check, as raw SQL would.
        AuditEntry(action='bogus', lawyer_profile=applicant, admin=admin).save_base(force_insert=True)


def test_an_unsaved_change_to_an_account_does_not_make_it_the_admin(applicant):
    someone = User.objects.create_user('someone@example.org')
    someone.is_staff = True
    with pytest.raises(ValueError):
        record(Action.INVITATION, applicant, admin=someone)


def test_the_admin_can_be_given_as_django_gives_the_logged_in_account(admin, applicant):
    # request.user is a lazy wrapper around the account.
    from django.utils.functional import SimpleLazyObject
    entry = record(Action.INVITATION, applicant, admin=SimpleLazyObject(lambda: admin))
    assert AuditEntry.objects.get(pk=entry.pk).admin == admin


@pytest.fixture
def case(applicant):
    applicant.verified_via, applicant.vouched_by = 'The bar directory', 'A defence organisation'
    applicant.save()
    applicant.change_status(LawyerProfile.Status.LAWYER)
    return make_case(applicant, 'A case')


@pytest.fixture
def clip():
    return Clip.objects.create(code_hash='0' * 64)


def test_a_lawyers_action_records_the_case_and_the_clip(case, clip):
    entry = record(Action.PREVIEW, case.lawyer_profile, case=case, clip=clip)
    stored = AuditEntry.objects.get(pk=entry.pk)
    assert (stored.action, stored.lawyer_profile, stored.admin) == (Action.PREVIEW, case.lawyer_profile, None)
    assert (stored.case, stored.clip_id, stored.core_count) == (case, clip.pk, None)
    assert list(case.audit_entries.all()) == [stored]


def test_a_search_records_the_numbers_of_clips_found(case):
    entry = record(Action.SEARCH, case.lawyer_profile, case=case, core_count=3, margin_count=0)
    stored = AuditEntry.objects.get(pk=entry.pk)
    assert (stored.core_count, stored.margin_count, stored.clip_id) == (3, 0, None)


def test_deleting_a_clip_leaves_its_entries_as_they_were(case, clip):
    entry = record(Action.DOWNLOAD, case.lawyer_profile, case=case, clip=clip)
    clip_id = clip.pk
    delete_clip(clip)
    assert AuditEntry.objects.get(pk=entry.pk).clip_id == clip_id


def fields_taken_by(action, clip):
    """The fields other than the case that `action` takes, so that only the case is missing."""
    if action in CLIP_ACTIONS:
        return {'clip': clip}
    if action == Action.SEARCH:
        return {'core_count': 1, 'margin_count': 1}
    return {}


@pytest.mark.parametrize('action', CASE_ACTIONS)
def test_actions_on_a_case_name_it(action, case, clip):
    with pytest.raises(IntegrityError), transaction.atomic():
        record(action, case.lawyer_profile, **fields_taken_by(action, clip))
    record(action, case.lawyer_profile, case=case, **fields_taken_by(action, clip))


@pytest.mark.parametrize('action', CLIP_ACTIONS)
def test_previews_and_downloads_name_the_clip(action, case):
    with pytest.raises(IntegrityError), transaction.atomic():
        record(action, case.lawyer_profile, case=case)


def test_a_search_records_both_numbers(case):
    for counts in [{}, {'core_count': 1}, {'margin_count': 1}]:
        with pytest.raises(IntegrityError), transaction.atomic():
            record(Action.SEARCH, case.lawyer_profile, case=case, **counts)


@pytest.mark.parametrize('extra', ['case', 'clip', 'count'])
def test_the_admins_account_actions_name_no_case_clip_or_count(extra, admin, case, clip):
    given = {'case': {'case': case}, 'clip': {'clip': clip}, 'count': {'core_count': 1, 'margin_count': 1}}[extra]
    with pytest.raises(IntegrityError), transaction.atomic():
        record(Action.SUSPENSION, case.lawyer_profile, admin=admin, **given)


def test_a_search_names_no_clip(case, clip):
    with pytest.raises(IntegrityError), transaction.atomic():
        record(Action.SEARCH, case.lawyer_profile, case=case, clip=clip, core_count=1, margin_count=1)


@pytest.mark.parametrize('counts', [{'core_count': 1}, {'margin_count': 1}])
def test_a_preview_names_no_count(counts, case, clip):
    with pytest.raises(IntegrityError), transaction.atomic():
        record(Action.PREVIEW, case.lawyer_profile, case=case, clip=clip, **counts)


@pytest.mark.parametrize('extra', ['clip', 'count'])
def test_a_case_creation_names_no_clip_or_count(extra, case, clip):
    given = {'clip': {'clip': clip}, 'count': {'core_count': 1, 'margin_count': 1}}[extra]
    with pytest.raises(IntegrityError), transaction.atomic():
        record(Action.CASE_CREATION, case.lawyer_profile, case=case, **given)


def test_an_entry_about_a_case_names_the_cases_own_lawyer(case):
    other = LawyerProfile.objects.create(user=User.objects.create_user('other@example.org'), bar='Lyon')
    with pytest.raises(ValueError):
        record(Action.CASE_CREATION, other, case=case)
    assert not AuditEntry.objects.exists()


@pytest.mark.parametrize('action', ADMIN_CASE_ACTIONS)
def test_the_admin_s_decision_on_a_case_names_the_admin_the_case_and_its_lawyer(action, admin, case):
    entry = record(action, case.lawyer_profile, admin=admin, case=case)
    stored = AuditEntry.objects.get(pk=entry.pk)
    assert (stored.action, stored.admin, stored.case, stored.lawyer_profile) == (action, admin, case, case.lawyer_profile)
    for missing in [{'case': case}, {'admin': admin}]:
        with pytest.raises(IntegrityError), transaction.atomic():
            record(action, case.lawyer_profile, **missing)


LIMITS = {'before': None, 'after': {'CASE_AREA_CAP_METRES': 2000}}


def test_a_change_of_limits_names_nobody_and_records_the_numbers():
    entry = record(Action.LIMITS_CHANGE, None, limits=LIMITS)
    stored = AuditEntry.objects.get(pk=entry.pk)
    assert (stored.limits, stored.lawyer_profile, stored.admin, stored.case) == (LIMITS, None, None, None)
    with pytest.raises(IntegrityError), transaction.atomic():
        record(Action.LIMITS_CHANGE, None)


def test_a_change_of_limits_names_no_person(admin, applicant):
    with pytest.raises(IntegrityError), transaction.atomic():
        record(Action.LIMITS_CHANGE, applicant, limits=LIMITS)
    with pytest.raises(IntegrityError), transaction.atomic():
        record(Action.LIMITS_CHANGE, None, admin=admin, limits=LIMITS)


def test_every_other_entry_names_a_person_and_no_limits(admin, applicant, case):
    with pytest.raises(IntegrityError), transaction.atomic():
        record(Action.INVITATION, None, admin=admin)
    with pytest.raises(IntegrityError), transaction.atomic():
        record(Action.INVITATION, applicant, admin=admin, limits=LIMITS)
    with pytest.raises(IntegrityError), transaction.atomic():
        record(Action.CASE_CREATION, case.lawyer_profile, case=case, limits=LIMITS)
