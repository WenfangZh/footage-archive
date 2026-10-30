"""Reports of a wrong time or place (SPEC.md §7, M6.2): sent through a usable case of the lawyer's
own that matches the clip, they reach the code holder as a message in the lawyer's thread, with an
optional note, which the code holder can answer; logged once, as the report, and never cancelled."""

import datetime

import pytest
from django.test import Client
from django.urls import reverse

from audit.models import Action, AuditEntry
from cases.models import Case
from clips import vault
from clips.models import Message
from tests.conftest import TEST_CODE, make_case
from tests.test_lawyer_messages import ENGLISH, START, Lawyer, clip, lawyer, shown_texts  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db


def report(lawyer, clip, case=None, **fields):  # noqa: F811
    url = reverse('lawyer:report_wrong', kwargs={'case_id': (case or lawyer.case).pk, 'clip_id': clip.pk})
    return lawyer.client.post(url, fields, headers=ENGLISH)


def code_holder_page():
    return Client().post(reverse('clips:code_holder'), {'code': TEST_CODE}, headers=ENGLISH).content.decode()


def test_a_report_of_a_wrong_time_reaches_the_code_holder_with_its_note_and_is_logged_once(lawyer, clip):  # noqa: F811
    response = report(lawyer, clip, kind='wrong_time', note='The police report says 16:40.')

    assert response.status_code == 302
    message = Message.objects.get()
    assert (message.kind, message.direction, message.lawyer_profile) == (
        Message.Kind.WRONG_TIME, Message.Direction.TO_CODE_HOLDER, lawyer.profile,
    )
    assert b'16:40' not in bytes(message.ciphertext)
    assert [text for _message, text in vault.code_holder_messages(clip, TEST_CODE)] == ['The police report says 16:40.']
    page = code_holder_page()
    assert 'Report of a wrong time' in page and 'The police report says 16:40.' in page
    entries = AuditEntry.objects.filter(clip_id=clip.pk).exclude(action=Action.CLIP_VIEW)
    assert list(entries.values_list('action', 'lawyer_profile', 'case')) == [
        (Action.REPORT_WRONG_TIME, lawyer.profile.pk, lawyer.case.pk),
    ]


def test_a_report_of_a_wrong_place_needs_no_note(lawyer, clip):  # noqa: F811
    report(lawyer, clip, kind='wrong_place')

    message = Message.objects.get()
    assert message.kind == Message.Kind.WRONG_PLACE and message.ciphertext is None
    page = code_holder_page()
    assert 'Report of a wrong place' in page and 'No note.' in page
    assert AuditEntry.objects.filter(action=Action.REPORT_WRONG_PLACE, case=lawyer.case).exists()


def test_the_code_holder_answers_a_report_and_the_lawyer_reads_both_on_their_messages_page(lawyer, clip):  # noqa: F811
    report(lawyer, clip, kind='wrong_time', note='The police report says 16:40.')
    first = Message.objects.get()

    answered = Client().post(reverse('clips:reply'), {'code': TEST_CODE, 'thread': first.pk, 'text': 'My phone was an hour off.'}, headers=ENGLISH)

    assert answered.status_code == 200
    page = lawyer.page()
    assert shown_texts(page, lawyer.private_key) == ['The police report says 16:40.', 'My phone was an hour off.']
    assert 'Report of a wrong time' in page.content.decode()


def test_a_report_goes_only_through_a_usable_case_of_the_lawyers_own_that_matches_the_clip(lawyer, clip):  # noqa: F811
    elsewhere = make_case(lawyer.profile, starts_at=START + datetime.timedelta(days=30), ends_at=START + datetime.timedelta(days=30, hours=1))
    closed = make_case(lawyer.profile, starts_at=START, ends_at=START + datetime.timedelta(hours=1))
    Case.objects.filter(pk=closed.pk).close(Case.ClosedBecause.LAWYER)
    other = Lawyer('other@example.org')

    for case in [elsewhere, closed, other.case]:
        assert report(lawyer, clip, case=case, kind='wrong_time').status_code == 404
    assert not Message.objects.exists()
    assert not AuditEntry.objects.filter(action=Action.REPORT_WRONG_TIME).exists()


def test_a_report_of_a_wrong_time_or_place_cannot_be_cancelled(lawyer, clip):  # noqa: F811
    report(lawyer, clip, kind='wrong_time')

    for kind in ['wrong_time', 'wrong_place']:
        url = reverse('lawyer:cancel_report', kwargs={'case_id': lawyer.case.pk, 'clip_id': clip.pk, 'kind': kind})
        assert lawyer.client.post(url).status_code == 404
    assert Message.objects.count() == 1


def test_only_a_time_or_a_place_can_be_reported_wrong(lawyer, clip):  # noqa: F811
    report(lawyer, clip, kind='message', note='Not a report.')

    assert not Message.objects.exists()
