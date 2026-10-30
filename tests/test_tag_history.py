"""The admin's tag history of a clip (SPEC.md §7, M6.1): what lawyers did to its tags, by whom,
and when the code holder changed theirs; drawn from the audit log and the clip's edits, and seen
by no lawyer."""

import datetime
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from accounts.models import LawyerProfile
from audit.models import Action, record
from clips import upload_codes
from clips.models import Clip, ClipTag, Edit
from tests.conftest import TEST_CODE, log_in_with_second_factor, make_case

pytestmark = pytest.mark.django_db

ENGLISH = {'accept-language': 'en'}


def lawyer(last_name):
    profile = LawyerProfile.objects.create(
        user=get_user_model().objects.create_user(f'{last_name.lower()}@example.org', last_name=last_name), bar='Lyon',
    )
    profile.verified_via, profile.vouched_by = 'The bar directory', 'A defence organisation'
    profile.save()
    profile.change_status(LawyerProfile.Status.LAWYER)
    return profile


def act(action, profile, clip, tag=None):
    """As a lawyer's action writes it: through one of their cases."""
    record(action, profile, case=make_case(profile), clip=clip, clip_tag=tag)


@pytest.fixture
def clip():
    return Clip.objects.create(code_hash=upload_codes.keyed_hash(TEST_CODE), status=Clip.Status.READY)


@pytest.fixture
def admin_client():
    return log_in_with_second_factor(Client(), get_user_model().objects.create_superuser('admin@example.org'))


def history(client, clip_id):
    return client.get(reverse('manage:tag_history', kwargs={'clip_id': clip_id}), headers=ENGLISH)


def items(response):
    return [item for item in response.content.decode().split('<li>')[1:]]


def test_the_admin_sees_who_did_what_to_which_tag(admin_client, clip):
    arrest = ClipTag.objects.create(clip=clip, key='arrest', source=ClipTag.Source.CODE_HOLDER)
    added = ClipTag.objects.create(clip=clip, custom_name='Water cannon', source=ClipTag.Source.LAWYER)
    act(Action.TAG_ADDITION, lawyer('Martin'), clip, added)
    act(Action.TAG_REMOVAL_PROPOSAL, lawyer('Bernard'), clip, arrest)

    response = history(admin_client, clip.pk)

    assert response.status_code == 200
    first, second = items(response)
    assert 'Tag addition' in first and 'Water cannon' in first and '(lawyer)' in first and 'Martin' in first
    assert 'Proposal to remove a tag' in second and 'Arrest' in second and '(code holder)' in second
    assert 'Bernard' in second and 'bernard@example.org' in second


def test_a_deleted_tag_shows_without_its_name_and_the_code_holders_changes_by_their_block(admin_client, clip):
    dropped = ClipTag.objects.create(clip=clip, custom_name='Barricade', source=ClipTag.Source.CODE_HOLDER)
    act(Action.TAG_REMOVAL_PROPOSAL, lawyer('Martin'), clip, dropped)
    # The code holder drops the tag later; the edit says only that their tags changed.
    dropped.delete()
    Edit.objects.create(clip=clip, field=Edit.EditableField.TAGS, edited_at=datetime.datetime(2099, 1, 1, 9, tzinfo=datetime.UTC))
    Edit.objects.create(clip=clip, field=Edit.EditableField.CONTEXT)

    proposal, change = items(history(admin_client, clip.pk))

    assert 'a tag since deleted' in proposal and 'Barricade' not in proposal
    assert 'the code holder changed their tags' in change
    # 09:00 UTC is 10:00 in Paris, in the block from 08:00 (SPEC.md §6).
    assert '1 January 2099, in the four hours from 08:00' in change


def test_another_clips_entries_are_not_in_its_history(admin_client, clip):
    other = Clip.objects.create(code_hash=upload_codes.keyed_hash('another code'), status=Clip.Status.READY)
    tag = ClipTag.objects.create(clip=other, key='arrest', source=ClipTag.Source.CODE_HOLDER)
    act(Action.TAG_REMOVAL_PROPOSAL, lawyer('Martin'), other, tag)
    act(Action.CLIP_VIEW, lawyer('Bernard'), clip)

    response = history(admin_client, clip.pk)

    assert response.status_code == 200
    assert items(response) == []


def test_a_deleted_clips_history_stays_and_an_unknown_clip_has_none(admin_client, clip):
    tag = ClipTag.objects.create(clip=clip, key='arrest', source=ClipTag.Source.CODE_HOLDER)
    act(Action.TAG_REMOVAL_PROPOSAL, lawyer('Martin'), clip, tag)
    clip_id = clip.pk
    clip.delete()

    assert len(items(history(admin_client, clip_id))) == 1
    assert history(admin_client, uuid.uuid4()).status_code == 404


def test_no_lawyer_sees_the_tag_history(clip):
    tag = ClipTag.objects.create(clip=clip, key='arrest', source=ClipTag.Source.CODE_HOLDER)
    profile = lawyer('Martin')
    act(Action.TAG_REMOVAL_PROPOSAL, profile, clip, tag)
    client = log_in_with_second_factor(Client(), profile.user)

    response = history(client, clip.pk)

    assert response.status_code == 403
    assert 'Martin' not in response.content.decode()


def test_the_disputed_list_shows_each_clip_with_a_proposal_by_identifier_latest_first(admin_client, clip):
    quiet = Clip.objects.create(code_hash=upload_codes.keyed_hash('a quiet clip'), status=Clip.Status.READY)
    added = ClipTag.objects.create(clip=quiet, custom_name='Water cannon', source=ClipTag.Source.LAWYER)
    act(Action.TAG_ADDITION, lawyer('Martin'), quiet, added)
    older = Clip.objects.create(code_hash=upload_codes.keyed_hash('an older clip'), status=Clip.Status.READY)
    dropped = ClipTag.objects.create(clip=older, custom_name='Barricade', source=ClipTag.Source.CODE_HOLDER)
    act(Action.TAG_REMOVAL_PROPOSAL, lawyer('Bernard'), older, dropped)
    tag = ClipTag.objects.create(clip=clip, key='arrest', source=ClipTag.Source.CODE_HOLDER)
    act(Action.TAG_RESTORATION_PROPOSAL, lawyer('Petit'), clip, tag)
    # Still listed once the code holder has deleted the tag in dispute.
    dropped.delete()

    response = admin_client.get(reverse('manage:disputed_tags'), headers=ENGLISH)

    assert response.status_code == 200
    listed = items(response)
    assert [str(clip.pk) in listed[0], str(older.pk) in listed[1]] == [True, True]
    assert len(listed) == 2
    assert reverse('manage:tag_history', kwargs={'clip_id': clip.pk}) in listed[0]
    # By identifier only: no tag, and no lawyer.
    assert not any(word in response.content.decode() for word in ['Barricade', 'Arrest', 'Bernard', 'Petit'])


def test_no_lawyer_sees_the_disputed_list():
    client = log_in_with_second_factor(Client(), lawyer('Martin').user)
    assert client.get(reverse('manage:disputed_tags')).status_code == 403
