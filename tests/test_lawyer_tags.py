"""Lawyers' tags (SPEC.md §7, M6.1): added through a matching case, removed and restored by three
different lawyers, never shown to the code holder, logged by the tag's id."""

import datetime

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from accounts.models import LawyerProfile
from audit.models import Action, AuditEntry
from clips import grid, upload_codes
from cases.models import Case
from clips.models import Clip, ClipTag, Precision
from tests.conftest import BASTILLE, TEST_CODE, log_in_with_second_factor, make_case, with_code_holder_key
from tests.test_code_holder import EDIT_URL, ENGLISH, ENTRY_URL, form_values

pytestmark = pytest.mark.django_db

START = datetime.datetime(2026, 6, 15, 16, tzinfo=datetime.UTC)
CENTRE = grid.cell_for_point(*BASTILLE, 11)


class Lawyer:
    """An admitted lawyer with a case the clip matches, logged in."""

    def __init__(self, email):
        self.profile = LawyerProfile.objects.create(user=get_user_model().objects.create_user(email), bar='Paris')
        self.profile.verified_via, self.profile.vouched_by = 'The bar directory', 'A defence organisation'
        self.profile.save()
        self.profile.change_status(LawyerProfile.Status.LAWYER)
        self.case = make_case(self.profile, starts_at=START, ends_at=START + datetime.timedelta(hours=1))
        self.client = log_in_with_second_factor(Client(), self.profile.user)

    def url(self, name, clip, **kwargs):
        return reverse(f'lawyer:{name}', kwargs={'case_id': self.case.pk, 'clip_id': clip.pk, **kwargs})

    def page(self, clip):
        return self.client.get(self.url('clip', clip), headers=ENGLISH)

    def add(self, clip, **fields):
        return self.client.post(self.url('add_tag', clip), {'key': '', 'custom_name': '', **fields}, headers=ENGLISH)

    def propose(self, clip, tag, direction=None):
        """As the page's button does: towards the other state of the tag as shown."""
        if direction is None:
            direction = 'restore' if ClipTag.objects.get(pk=tag.pk).removed else 'remove'
        return self.client.post(self.url('propose_tag', clip, tag_id=tag.pk), {'direction': direction})

    def cancel(self, clip, tag):
        return self.client.post(self.url('cancel_tag', clip, tag_id=tag.pk))


@pytest.fixture
def lawyers():
    return [Lawyer(f'lawyer{number}@example.org') for number in range(4)]


@pytest.fixture
def clip():
    clip = Clip.objects.create(
        code_hash=upload_codes.keyed_hash(TEST_CODE), status=Clip.Status.READY,
        stated_time=START + datetime.timedelta(minutes=10), stated_cell=CENTRE, stated_precision=Precision.EXACT_SPOT,
        as_uploaded_stated_time=START, as_uploaded_stated_cell=CENTRE, as_uploaded_stated_precision=Precision.EXACT_SPOT,
        as_uploaded_tags={'default': ['arrest'], 'custom': ['Barricade']},
    )
    ClipTag.objects.create(clip=clip, key='arrest', source=ClipTag.Source.CODE_HOLDER)
    ClipTag.objects.create(clip=clip, custom_name='Barricade', source=ClipTag.Source.CODE_HOLDER)
    return with_code_holder_key(clip)


def code_holder_page(clip):
    return Client().post(ENTRY_URL, {'code': TEST_CODE}, headers=ENGLISH).content.decode()


def code_holder_tags(page):
    """The tags the code holder's form shows as theirs: the default tags ticked, and the others."""
    values = form_values(page)
    return values.get('tags', []), values.get('custom_tags')


def shown(lawyer, clip):
    """The names of the tags the lawyer sees on the clip, and of those removed."""
    context = lawyer.page(clip).context
    return [tag['name'] for tag in context['tags']], [tag['name'] for tag in context['removed_tags']]


def test_a_lawyer_adds_a_tag_that_other_lawyers_see_as_a_lawyers_and_the_code_holder_never_does(lawyers, clip):
    first, other = lawyers[:2]
    first.add(clip, custom_name='Officer with a shield')
    first.add(clip, key='kettling')
    page = other.page(clip).content.decode()
    assert 'Officer with a shield <small>(lawyer)</small>' in page
    assert 'class="tag-source-lawyer">Kettling' in page and 'class="tag-source-code_holder">Arrest' in page
    # Never which lawyer: not even the lawyer's own name, which the account holds.
    first.profile.user.first_name = 'Camille'
    first.profile.user.save()
    assert 'Camille' not in other.page(clip).content.decode()
    holder = code_holder_page(clip)
    assert 'Officer with a shield' not in holder
    assert code_holder_tags(holder) == (['arrest'], 'Barricade')


def test_a_tag_is_added_only_through_a_usable_case_the_clip_matches(lawyers, clip):
    lawyer = lawyers[0]
    Case.objects.filter(pk=lawyer.case.pk).close(Case.ClosedBecause.LAWYER)
    assert lawyer.add(clip, key='kettling').status_code == 404
    elsewhere = Lawyer('elsewhere@example.org')
    elsewhere.case = make_case(elsewhere.profile, starts_at=START - datetime.timedelta(days=30),
                               ends_at=START - datetime.timedelta(days=30, minutes=-60))
    assert elsewhere.add(clip, key='kettling').status_code == 404
    assert not ClipTag.objects.filter(source=ClipTag.Source.LAWYER).exists()


@pytest.mark.parametrize('fields', [
    {'key': 'arrest'}, {'custom_name': '  barricade '}, {'custom_name': 'Interpellation'}, {'custom_name': 'ARREST'},
])
def test_a_tag_the_clip_already_has_cannot_be_added(lawyers, clip, fields):
    lawyers[0].add(clip, **fields)
    assert not ClipTag.objects.filter(source=ClipTag.Source.LAWYER).exists()


def test_the_same_text_typed_another_way_is_the_same_tag(lawyers, clip):
    # 'Barricade' with a combining accent would differ only in how it is encoded: here a
    # custom tag of the code holder's, typed composed, and a lawyer's, decomposed.
    ClipTag.objects.create(clip=clip, custom_name='Rue de l\u2019Épée', source=ClipTag.Source.CODE_HOLDER)
    lawyers[0].add(clip, custom_name='rue de l\u2019E\u0301pe\u0301e')
    lawyers[0].add(clip, custom_name='\u200b')
    assert not ClipTag.objects.filter(source=ClipTag.Source.LAWYER).exists()


def test_a_custom_tag_named_like_a_default_tag_is_that_tag(lawyers, clip):
    lawyers[0].add(clip, custom_name='Gaz lacrymogène')
    assert ClipTag.objects.get(source=ClipTag.Source.LAWYER).key == 'tear_gas'


def test_three_different_lawyers_remove_a_tag_and_three_restore_it(lawyers, clip, settings):
    settings.LANGUAGE_CODE = 'en'
    arrest = ClipTag.objects.get(key='arrest')
    for lawyer in lawyers[:2]:
        lawyer.propose(clip, arrest)
    assert 'Arrest' in shown(lawyers[3], clip)[0]
    lawyers[2].propose(clip, arrest)
    assert shown(lawyers[3], clip) == (['Barricade'], ['Arrest'])
    # Removed for lawyers only: the code holder and the as-uploaded values are untouched.
    assert code_holder_tags(code_holder_page(clip)) == (['arrest'], 'Barricade')
    assert Clip.objects.get().as_uploaded_tags == {'default': ['arrest'], 'custom': ['Barricade']}
    # A removed tag cannot be added again, only restored.
    lawyers[3].add(clip, key='arrest')
    assert ClipTag.objects.filter(key='arrest').count() == 1
    # Counting starts again: the lawyers who removed it can propose restoring it.
    for lawyer in lawyers[:2]:
        lawyer.propose(clip, arrest)
    assert shown(lawyers[3], clip)[1] == ['Arrest']
    lawyers[3].propose(clip, arrest)
    assert sorted(shown(lawyers[3], clip)[0]) == ['Arrest', 'Barricade'] and shown(lawyers[3], clip)[1] == []


def test_a_lawyers_proposals_count_once_and_a_cancelled_one_not_at_all(lawyers, clip):
    arrest = ClipTag.objects.get(key='arrest')
    lawyers[0].propose(clip, arrest)
    lawyers[0].propose(clip, arrest)
    lawyers[1].propose(clip, arrest)
    lawyers[1].cancel(clip, arrest)
    lawyers[2].propose(clip, arrest)
    assert not ClipTag.objects.get(pk=arrest.pk).removed
    lawyers[1].propose(clip, arrest)
    assert ClipTag.objects.get(pk=arrest.pk).removed


def test_a_page_shown_before_the_tag_changed_casts_no_vote_the_other_way(lawyers, clip):
    arrest = ClipTag.objects.get(key='arrest')
    for lawyer in lawyers[:3]:
        lawyer.propose(clip, arrest)
    # Lawyer 3's page still offered to remove it.
    lawyers[3].propose(clip, arrest, direction='remove')
    assert not ClipTag.objects.get(pk=arrest.pk).proposals.exists()
    assert not AuditEntry.objects.filter(action=Action.TAG_RESTORATION_PROPOSAL).exists()


def test_a_suspended_lawyers_proposal_does_not_count(lawyers, clip):
    arrest = ClipTag.objects.get(key='arrest')
    for lawyer in lawyers[:2]:
        lawyer.propose(clip, arrest)
    lawyers[0].profile.change_status(LawyerProfile.Status.SUSPENDED)
    lawyers[2].propose(clip, arrest)
    assert not ClipTag.objects.get(pk=arrest.pk).removed


def test_a_code_holders_edit_keeps_a_removed_tag_removed(lawyers, clip):
    arrest = ClipTag.objects.get(key='arrest')
    for lawyer in lawyers[:3]:
        lawyer.propose(clip, arrest)
    client = Client()
    values = form_values(client.post(ENTRY_URL, {'code': TEST_CODE}, headers=ENGLISH).content.decode())
    values.update(tags=['arrest', 'tear_gas'])
    client.post(EDIT_URL, {key: value for key, value in values.items() if value is not None}, headers=ENGLISH)
    assert ClipTag.objects.get(key='arrest').removed
    assert ClipTag.objects.filter(key='tear_gas', source=ClipTag.Source.CODE_HOLDER).exists()


def test_each_action_on_a_tag_is_logged_by_its_id_never_its_text(lawyers, clip):
    lawyers[0].add(clip, custom_name='Officer with a shield')
    tag = ClipTag.objects.get(source=ClipTag.Source.LAWYER)
    lawyers[0].propose(clip, tag)
    lawyers[0].cancel(clip, tag)
    for lawyer in lawyers[:3]:
        lawyer.propose(clip, tag)
    for lawyer in lawyers[:3]:
        lawyer.propose(clip, tag)
    entries = AuditEntry.objects.filter(clip_tag_id=tag.pk).order_by('pk')
    assert [entry.action for entry in entries] == [
        Action.TAG_ADDITION, Action.TAG_REMOVAL_PROPOSAL, Action.TAG_PROPOSAL_CANCELLING,
        Action.TAG_REMOVAL_PROPOSAL, Action.TAG_REMOVAL_PROPOSAL, Action.TAG_REMOVAL_PROPOSAL, Action.TAG_REMOVAL,
        Action.TAG_RESTORATION_PROPOSAL, Action.TAG_RESTORATION_PROPOSAL, Action.TAG_RESTORATION_PROPOSAL,
        Action.TAG_RESTORATION,
    ]
    assert all(entry.case.lawyer_profile == entry.lawyer_profile and entry.clip_id == clip.pk for entry in entries)
    # The removal and the restoration name the lawyer whose proposal was the third.
    assert [entry.lawyer_profile for entry in entries if entry.action in (Action.TAG_REMOVAL, Action.TAG_RESTORATION)] == [
        lawyers[2].profile, lawyers[2].profile,
    ]
    assert 'shield' not in repr(list(AuditEntry.objects.values()))


def test_a_tag_of_another_clip_is_not_found(lawyers, clip):
    other_clip = Clip.objects.create(code_hash='2' * 64)
    other_tag = ClipTag.objects.create(clip=other_clip, key='kettling', source=ClipTag.Source.CODE_HOLDER)
    assert lawyers[0].propose(clip, other_tag).status_code == 404


def test_the_tag_colours_come_from_the_settings_for_lawyers_only(lawyers, settings):
    settings.TAG_COLOUR_LAWYER = '#123456'
    css = lawyers[0].client.get(reverse('lawyer:tag_colours')).content.decode()
    assert '.tag-source-code_holder { border-left: 0.5em solid #0072b2' in css
    assert '.tag-source-lawyer { border-left: 0.5em solid #123456' in css
    assert Client().get(reverse('lawyer:tag_colours')).status_code == 302
