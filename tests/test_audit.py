import datetime
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, models, transaction
from django.db.models import ProtectedError

from accounts.models import LawyerProfile
from audit.models import Action, AppendOnly, AuditEntry, record
from clips.editing import delete_clip
from clips.models import Clip, ClipTag
from keys.grants import current_key
from keys.models import GroupKey
from tests.conftest import make_case, rotation_attempt

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


def test_deleting_a_clip_leaves_its_entries_as_they_were(case, clip):
    entry = record(Action.DOWNLOAD, case.lawyer_profile, case=case, clip=clip)
    clip_id = clip.pk
    delete_clip(clip)
    assert AuditEntry.objects.get(pk=entry.pk).clip_id == clip_id


@pytest.fixture
def tag(clip):
    return ClipTag.objects.create(clip=clip, key='arrest', source=ClipTag.Source.CODE_HOLDER)


def test_an_entry_about_a_tag_names_the_tags_own_clip(case, clip, tag):
    with pytest.raises(ValueError):
        record(
            Action.TAG_ADDITION, case.lawyer_profile, case=case, clip=Clip.objects.create(code_hash='1' * 64), clip_tag=tag,
        )


# What each entry names, written out here rather than taken from audit.models, so that an action
# filed in the wrong group there fails. The people, case, clip and tag follow SPEC.md §7 (the
# admin's actions name the admin; a lawyer's, their case); the key, rotation and attempt follow
# docs/m5-encryption-design.md §5.3, §5.5 and §5.6, and the admin's confirmation of a new key
# keys/recovery.py. Every action is listed, with every field it names; it names no other.
ADMIN_ON = {'admin', 'lawyer'}
CASE_ACTION = {'lawyer', 'case'}
ON_A_CLIP = {'lawyer', 'case', 'clip'}
ON_A_TAG = {'lawyer', 'case', 'clip', 'tag'}
NAMES = {
    Action.INVITATION: ADMIN_ON, Action.DECLINING: ADMIN_ON, Action.ADMISSION: ADMIN_ON,
    Action.SUSPENSION: ADMIN_ON, Action.REINSTATEMENT: ADMIN_ON, Action.REVOCATION: ADMIN_ON,
    Action.CASE_CREATION: CASE_ACTION, Action.CASE_CLOSING: CASE_ACTION, Action.LARGER_CASE_REQUEST: CASE_ACTION,
    Action.SEARCH: CASE_ACTION | {'core_count', 'margin_count'},
    Action.PREVIEW: ON_A_CLIP, Action.DOWNLOAD: ON_A_CLIP, Action.CLIP_VIEW: ON_A_CLIP,
    Action.CASE_APPROVAL: ADMIN_ON | {'case'}, Action.CASE_REFUSAL: ADMIN_ON | {'case'},
    # About the archive's settings, not a person.
    Action.LIMITS_CHANGE: {'limits'},
    Action.ALERT_LIMIT_RAISE: ADMIN_ON | {'limits'},
    Action.GROUP_KEY_CREATION: {'lawyer'}, Action.STARTING_OVER: {'lawyer'},
    Action.KEY_GRANT: {'lawyer', 'key'}, Action.PASSPHRASE_REPLACEMENT: {'lawyer', 'key'},
    Action.KEY_RESET: {'lawyer', 'key'}, Action.KEY_EXPOSURE_REPORT: {'lawyer', 'key'},
    Action.KEY_CONFIRMATION: ADMIN_ON | {'key'},
    Action.ROTATION_REQUEST: {'lawyer', 'rotation'},
    Action.ROTATION_ATTEMPT: {'lawyer', 'attempt'}, Action.ROTATION_NEW_KEY: {'lawyer', 'attempt'},
    Action.ROTATION_CHECK_PASSED: {'lawyer', 'attempt'}, Action.ROTATION_CHECK_FAILED: {'lawyer', 'attempt'},
    Action.ROTATION_REJECTION: {'lawyer', 'attempt'}, Action.ROTATION_ABANDON: {'lawyer', 'attempt'},
    Action.ROTATION_END: {'lawyer', 'attempt'},
    Action.TAG_ADDITION: ON_A_TAG, Action.TAG_REMOVAL_PROPOSAL: ON_A_TAG, Action.TAG_RESTORATION_PROPOSAL: ON_A_TAG,
    Action.TAG_PROPOSAL_CANCELLING: ON_A_TAG, Action.TAG_REMOVAL: ON_A_TAG, Action.TAG_RESTORATION: ON_A_TAG,
    Action.MESSAGE: ON_A_CLIP, Action.REPORT_WRONG_TIME: ON_A_CLIP, Action.REPORT_WRONG_PLACE: ON_A_CLIP,
    Action.REPORT_DOUBT: ON_A_CLIP, Action.REPORT_INAPPROPRIATE: ON_A_CLIP, Action.DOUBT_CANCELLING: ON_A_CLIP,
    Action.INAPPROPRIATE_CANCELLING: ON_A_CLIP, Action.CLIP_WITHDRAWAL: ON_A_CLIP,
    # From the lawyer's messages page, which needs no case: a closed case must not cut a thread off.
    Action.REPLY: {'lawyer', 'clip'},
    # No lawyer, so as not to single out the lawyers who reported the clip.
    Action.WITHDRAWN_CLIP_DELETION: {'admin', 'clip'},
}


def test_every_action_says_what_it_names():
    assert set(NAMES) == set(Action)


def test_each_entry_names_what_its_action_names_and_nothing_else(admin, case, clip, tag):
    attempt = rotation_attempt(GroupKey.Status.CHECKING)
    # Each name's field, as record() takes it.
    values = {
        'admin': {'admin': admin}, 'lawyer': {'lawyer_profile': case.lawyer_profile}, 'case': {'case': case},
        'clip': {'clip': clip}, 'tag': {'clip_tag': tag}, 'core_count': {'core_count': 3}, 'margin_count': {'margin_count': 0},
        'key': {'lawyer_key': current_key(case.lawyer_profile)}, 'rotation': {'rotation': attempt.rotation},
        'attempt': {'rotation_attempt': attempt}, 'limits': None,
    }
    # The stored entry gives the clip and tag back as ids.
    stored_as = {'clip': ('clip_id', clip.pk), 'tag': ('clip_tag_id', tag.pk)}

    for action, names in NAMES.items():
        values['limits'] = {'limits': RAISED if action == Action.ALERT_LIMIT_RAISE else LIMITS}

        def given(names):
            return {'lawyer_profile': None, **{field: v for name in names for field, v in values[name].items()}}

        stored = AuditEntry.objects.get(pk=record(action, **given(names)).pk)
        for name, fields in values.items():
            field, value = stored_as.get(name, next(iter(fields.items())))
            assert getattr(stored, field) == (value if name in names else None), (action, field)

        for name in values:
            try:
                with transaction.atomic():
                    record(action, **given(names ^ {name}))
            except (IntegrityError, ValueError):
                continue
            pytest.fail(f'{action} was stored with {name} {"left out" if name in names else "added"}')


def test_deleting_a_tag_leaves_its_entries_as_they_were(case, clip, tag):
    entry = record(Action.TAG_REMOVAL_PROPOSAL, case.lawyer_profile, case=case, clip=clip, clip_tag=tag)
    tag_id = tag.pk
    tag.delete()
    assert AuditEntry.objects.get(pk=entry.pk).clip_tag_id == tag_id


def test_an_entry_about_a_case_names_the_cases_own_lawyer(case):
    other = LawyerProfile.objects.create(user=User.objects.create_user('other@example.org'), bar='Lyon')
    with pytest.raises(ValueError):
        record(Action.CASE_CREATION, other, case=case)
    assert not AuditEntry.objects.exists()


LIMITS = {'before': None, 'after': {'CASE_AREA_CAP_METRES': 2000}}


RAISED = {'before': {'ALERT_DOWNLOADS': 20}, 'after': {'ALERT_DOWNLOADS': 60}, 'days': 2}


@pytest.mark.parametrize('limits_given', [
    {'before': None, 'after': {'ALERT_DOWNLOADS': 60}, 'days': 2},
    {'before': {'ALERT_DOWNLOADS': 20}, 'after': {'ALERT_DOWNLOADS': 60}},
    {'before': {'ALERT_DOWNLOADS': 20}, 'after': {'ALERT_DOWNLOADS': 10}, 'days': 2},
    {'before': {'ALERT_DOWNLOADS': 20}, 'after': {'ALERT_CASES': 60}, 'days': 2},
    {'before': {'CASE_AREA_CAP_METRES': 2000}, 'after': {'CASE_AREA_CAP_METRES': 3000}, 'days': 2},
    {'before': {'ALERT_DOWNLOADS': 20}, 'after': {'ALERT_DOWNLOADS': 60}, 'days': 0},
    {'before': {'ALERT_DOWNLOADS': 20}, 'after': {'ALERT_DOWNLOADS': 60}, 'days': 'two'},
    {'before': {}, 'after': {}, 'days': 2},
])
def test_a_raise_records_one_higher_limit_and_its_days(admin, case, limits_given):
    with pytest.raises(ValueError):
        record(Action.ALERT_LIMIT_RAISE, case.lawyer_profile, admin=admin, limits=limits_given)
