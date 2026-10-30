"""Lawyers' reports that count (SPEC.md §7, M6.2): a doubt shows other lawyers a doubt marker, one
report of inappropriate content a content warning, and a second, from a different lawyer, withdraws
the clip for good; reports go through a matching case, are logged, and the code holder learns only
of a withdrawal."""

import datetime
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import LawyerProfile
from audit.models import REPORT_ACTIONS, Action, AuditEntry
from cases import clip_pages
from cases.models import Case
from clips import grid, upload_codes, vault
from clips.models import Clip, ClipTag, Message, Precision, Report, TagProposal
from keys.models import LawyerKey
from tests.conftest import BASTILLE, TEST_CODE, log_in_with_second_factor, make_case, with_code_holder_key

pytestmark = pytest.mark.django_db

START = datetime.datetime(2026, 6, 15, 16, tzinfo=datetime.UTC)
CENTRE = grid.cell_for_point(*BASTILLE, 11)
ENGLISH = {'accept-language': 'en'}
DOUBT, INAPPROPRIATE = Report.Kind.DOUBT, Report.Kind.INAPPROPRIATE
DOUBT_MARKER = 'A lawyer has raised a doubt about this clip.'
CONTENT_WARNING = 'Content warning: a lawyer has reported this clip as inappropriate.'


class Lawyer:
    """An admitted lawyer with a case the clip matches, logged in."""

    def __init__(self, email):
        self.profile = LawyerProfile.objects.create(user=get_user_model().objects.create_user(email), bar='Paris')
        self.profile.verified_via, self.profile.vouched_by = 'The bar directory', 'A defence organisation'
        self.profile.save()
        self.profile.change_status(LawyerProfile.Status.LAWYER)
        self.case = make_case(self.profile, starts_at=START, ends_at=START + datetime.timedelta(hours=1))
        self.client = log_in_with_second_factor(Client(), self.profile.user)

    def url(self, name, clip, case=None, **kwargs):
        return reverse(f'lawyer:{name}', kwargs={'case_id': (case or self.case).pk, 'clip_id': clip.pk, **kwargs})

    def page(self, clip):
        return self.client.get(self.url('clip', clip), headers=ENGLISH)

    def report(self, clip, kind, case=None, **fields):
        return self.client.post(self.url('report', clip, case=case, kind=kind), fields, headers=ENGLISH)

    def cancel(self, clip, kind):
        return self.client.post(self.url('cancel_report', clip, kind=kind), headers=ENGLISH)

    def found(self, clip):
        page = self.client.post(reverse('lawyer:search', kwargs={'case_id': self.case.pk})).content.decode()
        return str(clip.pk) in page


@pytest.fixture
def lawyers():
    return [Lawyer(f'lawyer{number}@example.org') for number in range(3)]


@pytest.fixture
def clip():
    clip = Clip.objects.create(
        code_hash=upload_codes.keyed_hash(TEST_CODE), status=Clip.Status.READY,
        stated_time=START + datetime.timedelta(minutes=10), stated_cell=CENTRE, stated_precision=Precision.EXACT_SPOT,
        as_uploaded_stated_time=START, as_uploaded_stated_cell=CENTRE, as_uploaded_stated_precision=Precision.EXACT_SPOT,
    )
    ClipTag.objects.create(clip=clip, key='arrest', source=ClipTag.Source.CODE_HOLDER)
    return with_code_holder_key(clip)


def code_holder_page(clip):
    # Without the CSRF tokens, which change on every page.
    content = Client().post(reverse('clips:code_holder'), {'code': TEST_CODE}, headers=ENGLISH).content.decode()
    return re.sub(r'[A-Za-z0-9]{64}', '', content)


def actions():
    """The reports and what they caused, as logged."""
    return list(AuditEntry.objects.filter(action__in=REPORT_ACTIONS).order_by('pk').values_list('action', 'lawyer_profile', 'case'))


def test_a_report_goes_only_through_a_usable_case_of_the_lawyers_own_that_matches_the_clip(lawyers, clip):
    lawyer, other = lawyers[:2]
    elsewhere = make_case(lawyer.profile, starts_at=START + datetime.timedelta(days=30), ends_at=START + datetime.timedelta(days=30, hours=1))
    closed = make_case(lawyer.profile, starts_at=START, ends_at=START + datetime.timedelta(hours=1))
    Case.objects.filter(pk=closed.pk).close(Case.ClosedBecause.LAWYER)

    for case in [elsewhere, closed, other.case]:
        assert lawyer.report(clip, DOUBT, case=case).status_code == 404
    assert not Report.objects.exists()
    assert not AuditEntry.objects.filter(action=Action.REPORT_DOUBT).exists()


def test_a_doubt_shows_other_lawyers_a_marker_with_no_text_and_is_logged(lawyers, clip):
    reporter, other = lawyers[:2]

    assert reporter.report(clip, DOUBT).status_code == 302

    assert DOUBT_MARKER in other.page(clip).content.decode()
    own_page = reporter.page(clip).content.decode()
    assert DOUBT_MARKER not in own_page and 'You raised a doubt' in own_page
    assert actions()[-1] == (Action.REPORT_DOUBT, reporter.profile.pk, reporter.case.pk)


def test_the_code_holder_is_told_of_neither_a_doubt_nor_a_first_report_of_inappropriate_content(lawyers, clip):
    code_holder_page(clip)
    before = code_holder_page(clip)

    lawyers[0].report(clip, DOUBT)
    lawyers[1].report(clip, INAPPROPRIATE)

    assert code_holder_page(clip) == before


def test_one_report_of_inappropriate_content_puts_a_content_warning_in_front_of_the_preview(lawyers, clip, monkeypatch):
    monkeypatch.setattr(clip_pages, 'reads_vault', lambda profile: True)
    monkeypatch.setattr(vault, 'has_file', lambda clip, kind: True)
    reporter, other = lawyers[:2]

    reporter.report(clip, INAPPROPRIATE)

    page = other.page(clip).content.decode()
    assert CONTENT_WARNING in page
    shown_anyway = page[page.index('<details>'):page.index('</details>')]
    assert 'Show the preview anyway' in shown_anyway and 'id="preview"' in shown_anyway
    assert other.found(clip)
    assert actions()[-1] == (Action.REPORT_INAPPROPRIATE, reporter.profile.pk, reporter.case.pk)


def test_a_second_report_from_a_different_lawyer_withdraws_the_clip_from_every_lawyer_page(lawyers, clip):
    first, second, third = lawyers

    first.report(clip, INAPPROPRIATE)
    # Withdrawing cannot be undone, so it is confirmed.
    assert second.report(clip, INAPPROPRIATE).status_code == 302
    assert Clip.objects.get(pk=clip.pk).status == Clip.Status.READY
    response = second.report(clip, INAPPROPRIATE, confirm_withdrawal='yes')

    assert response.status_code == 302 and response.url == reverse('lawyer:case', kwargs={'case_id': second.case.pk})
    assert Clip.objects.get(pk=clip.pk).status == Clip.Status.WITHDRAWN
    for lawyer in lawyers:
        assert not lawyer.found(clip)
        for name in ['clip', 'preview', 'download', 'hash_record']:
            assert lawyer.client.get(lawyer.url(name, clip)).status_code == 404
    assert actions()[-2:] == [
        (Action.REPORT_INAPPROPRIATE, second.profile.pk, second.case.pk),
        (Action.CLIP_WITHDRAWAL, second.profile.pk, second.case.pk),
    ]


def test_a_lawyers_reports_count_once(lawyers, clip):
    lawyer = lawyers[0]
    lawyer.report(clip, INAPPROPRIATE)
    lawyer.report(clip, INAPPROPRIATE, confirm_withdrawal='yes')

    assert Report.objects.count() == 1
    assert Clip.objects.get(pk=clip.pk).status == Clip.Status.READY


def test_a_suspended_lawyers_report_stops_counting(lawyers, clip):
    first, second, third = lawyers
    first.report(clip, INAPPROPRIATE)
    first.report(clip, DOUBT)
    first.profile.change_status(LawyerProfile.Status.SUSPENDED)

    page = third.page(clip).content.decode()
    second.report(clip, INAPPROPRIATE, confirm_withdrawal='yes')

    assert CONTENT_WARNING not in page and DOUBT_MARKER not in page
    assert Clip.objects.get(pk=clip.pk).status == Clip.Status.READY


def test_the_code_holder_sees_the_withdrawal_and_can_only_delete_the_clip(lawyers, clip):
    lawyers[0].report(clip, INAPPROPRIATE)
    lawyers[1].report(clip, INAPPROPRIATE, confirm_withdrawal='yes')
    client = Client()

    page = client.post(reverse('clips:code_holder'), {'code': TEST_CODE}, headers=ENGLISH).content.decode()
    refused = client.post(reverse('clips:edit'), {'code': TEST_CODE, 'stated_time': '2026-06-15T19:00'}, headers=ENGLISH)

    assert 'Your clip was withdrawn' in page
    assert f'action="{reverse("clips:edit")}"' not in page and f'action="{reverse("clips:delete")}"' in page
    assert refused.status_code == 409 and not clip.edits.exists()


def test_a_withdrawn_clips_threads_can_still_be_read_and_answered_on_both_sides(lawyers, clip):
    lawyer = lawyers[2]
    LawyerKey.objects.filter(lawyer_profile=lawyer.profile).update(admin_confirmed_at=timezone.now())
    first = vault.send_message(clip, lawyer.profile, Message.Direction.TO_CODE_HOLDER, 'Were you near the station?')
    lawyers[0].report(clip, INAPPROPRIATE)
    lawyers[1].report(clip, INAPPROPRIATE, confirm_withdrawal='yes')

    page = Client().post(reverse('clips:code_holder'), {'code': TEST_CODE}, headers=ENGLISH).content.decode()
    answered = Client().post(reverse('clips:reply'), {'code': TEST_CODE, 'thread': first.pk, 'text': 'Yes.'}, headers=ENGLISH)
    replied = lawyer.client.post(reverse('lawyer:reply', kwargs={'clip_id': clip.pk}), {'text': 'Thank you.'}, headers=ENGLISH)

    assert 'Were you near the station?' in page and 'read and answer' in page
    assert answered.status_code == 200 and replied.status_code == 302
    assert [text for _message, text in vault.code_holder_messages(clip, TEST_CODE)] == ['Were you near the station?', 'Yes.', 'Thank you.']


def test_a_cancelled_doubt_or_first_report_stops_counting_and_is_logged(lawyers, clip):
    first, second, third = lawyers
    first.report(clip, DOUBT)
    first.report(clip, INAPPROPRIATE)

    assert first.cancel(clip, DOUBT).status_code == 302
    assert first.cancel(clip, INAPPROPRIATE).status_code == 302

    page = third.page(clip).content.decode()
    assert DOUBT_MARKER not in page and CONTENT_WARNING not in page
    second.report(clip, INAPPROPRIATE, confirm_withdrawal='yes')
    assert Clip.objects.get(pk=clip.pk).status == Clip.Status.READY
    assert [action for action, _lawyer, _case in actions()[2:4]] == [Action.DOUBT_CANCELLING, Action.INAPPROPRIATE_CANCELLING]


def test_the_reports_behind_a_withdrawal_cannot_be_cancelled(lawyers, clip):
    first, second, _ = lawyers
    first.report(clip, INAPPROPRIATE)
    second.report(clip, INAPPROPRIATE, confirm_withdrawal='yes')

    for lawyer in (first, second):
        assert lawyer.cancel(clip, INAPPROPRIATE).status_code == 404
    assert Report.objects.count() == 2
    assert Clip.objects.get(pk=clip.pk).status == Clip.Status.WITHDRAWN


def test_deleting_a_clip_deletes_its_threads_reports_and_tag_proposals_and_keeps_their_audit_entries(lawyers, clip):
    first, second, third = lawyers
    first.report(clip, DOUBT)
    second.report(clip, INAPPROPRIATE)
    tag = ClipTag.objects.get(clip=clip)
    first.client.post(first.url('propose_tag', clip, tag_id=tag.pk), {'direction': 'remove'})
    LawyerKey.objects.filter(lawyer_profile=third.profile).update(admin_confirmed_at=timezone.now())
    third.client.post(third.url('send_message', clip), {'text': 'Were you near the station?'})
    assert Report.objects.count() == 2 and TagProposal.objects.count() == 1 and Message.objects.count() == 1
    logged = list(AuditEntry.objects.filter(clip_id=clip.pk).values_list('pk', 'action'))

    Client().post(reverse('clips:delete'), {'code': TEST_CODE, 'confirm_delete': 'yes'})

    assert not Clip.objects.exists()
    assert not Report.objects.exists() and not TagProposal.objects.exists() and not Message.objects.exists()
    assert {Action.REPORT_DOUBT, Action.REPORT_INAPPROPRIATE, Action.TAG_REMOVAL_PROPOSAL, Action.MESSAGE} <= {action for _pk, action in logged}
    assert list(AuditEntry.objects.filter(clip_id=clip.pk).values_list('pk', 'action')) == logged


def test_an_unknown_kind_of_report_is_not_found(lawyers, clip):
    assert lawyers[0].report(clip, 'wrong_time').status_code == 404
    assert not Report.objects.exists()
