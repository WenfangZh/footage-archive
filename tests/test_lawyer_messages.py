"""A lawyer's messages to a clip's code holder (SPEC.md §7, M6.2): sent through a usable case of
their own that matches the clip, read and answered on their messages page with no case, each
message and reply logged and reading not, and texts that only the lawyer's personal key opens on
that page."""

import base64
import datetime
import html
import json
import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from nacl import bindings
from nacl.public import PrivateKey, SealedBox

from accounts.models import LawyerProfile
from audit.models import Action, AuditEntry
from cases.models import Case
from clips import grid, upload_codes, vault
from clips.models import Clip, ClipTag, Message
from tests.conftest import BASTILLE, TEST_CODE, log_in_with_second_factor, make_case, make_key, with_code_holder_key

pytestmark = pytest.mark.django_db

START = datetime.datetime(2026, 6, 15, 16, tzinfo=datetime.UTC)
CENTRE = grid.cell_for_point(*BASTILLE, 11)
ENGLISH = {'accept-language': 'en'}
TEXT = 'Could you tell me what happened\njust before your video starts?'


class Lawyer:
    """An admitted lawyer with a confirmed personal key and a case the clip matches, logged in."""

    def __init__(self, email, confirmed=True):
        self.profile = LawyerProfile.objects.create(user=get_user_model().objects.create_user(email), bar='Paris')
        self.profile.verified_via, self.profile.vouched_by = 'The bar directory', 'A defence organisation'
        self.profile.save()
        self.profile.change_status(LawyerProfile.Status.LAWYER)
        self.private_key = PrivateKey.generate()
        key = make_key(self.profile, self.private_key)
        if confirmed:
            key.admin_confirmed_at = timezone.now()
            key.save()
        self.case = make_case(self.profile, starts_at=START, ends_at=START + datetime.timedelta(hours=1))
        self.client = log_in_with_second_factor(Client(), self.profile.user)

    def send(self, clip, text=TEXT, case=None, **fields):
        url = reverse('lawyer:send_message', kwargs={'case_id': (case or self.case).pk, 'clip_id': clip.pk})
        return self.client.post(url, {'text': text, **fields}, headers=ENGLISH)

    def reply(self, clip_id, text='And afterwards?', **fields):
        return self.client.post(reverse('lawyer:reply', kwargs={'clip_id': clip_id}), {'text': text, **fields}, headers=ENGLISH)

    def page(self):
        return self.client.get(reverse('lawyer:messages'), headers=ENGLISH)


@pytest.fixture
def lawyer():
    return Lawyer('lawyer@example.org')


@pytest.fixture
def clip():
    clip = Clip.objects.create(
        code_hash=upload_codes.keyed_hash(TEST_CODE), status=Clip.Status.READY,
        stated_time=START + datetime.timedelta(minutes=10), stated_cells=[CENTRE],
        as_uploaded_stated_time=START, as_uploaded_stated_cells=[CENTRE],
    )
    ClipTag.objects.create(clip=clip, custom_name='Barricade', source=ClipTag.Source.CODE_HOLDER)
    return with_code_holder_key(clip)


def shown_texts(response, private_key):
    """Each message's text as the lawyer's browser opens it (keys/static/keys/messages.js), from the
    page alone: the additional data rebuilt from what the page shows."""
    texts = []
    for attributes in re.findall(r'<p class="message-text"([^>]*)>', response.content.decode()):
        data = {name: html.unescape(value) for name, value in re.findall(r'data-([a-z]+)="([^"]*)"', attributes)}
        sealed = json.loads(data['sealed'])
        additional_data = ':'.join([
            'fa-message-v1', data['clip'], data['id'], data['lawyer'], data['position'], data['direction'], data['kind'],
            data['identified'],
        ]).encode()
        data_key = SealedBox(private_key).decrypt(base64.b64decode(sealed['sealed_key']))
        encrypted = base64.b64decode(sealed['ciphertext'])
        padded = bindings.crypto_aead_xchacha20poly1305_ietf_decrypt(
            encrypted[vault.NONCE_BYTES:], additional_data, encrypted[:vault.NONCE_BYTES], data_key,
        )
        texts.append(bindings.sodium_unpad(padded, vault.TEXT_BLOCK).decode())
    return texts


def test_a_message_through_a_matching_case_is_stored_encrypted_and_logged(lawyer, clip):
    response = lawyer.send(clip)

    assert response.status_code == 302
    message = Message.objects.get()
    assert (message.clip, message.lawyer_profile, message.direction) == (clip, lawyer.profile, Message.Direction.TO_CODE_HOLDER)
    assert b'happened' not in bytes(message.ciphertext)
    entry = AuditEntry.objects.get(action=Action.MESSAGE)
    assert (entry.lawyer_profile, entry.case, entry.clip_id) == (lawyer.profile, lawyer.case, clip.pk)
    assert [text for _message, text in vault.code_holder_messages(clip, TEST_CODE)] == [TEXT]


def test_no_message_through_a_case_the_clip_does_not_match_or_a_closed_case(lawyer, clip):
    elsewhere = make_case(lawyer.profile, starts_at=START + datetime.timedelta(days=30), ends_at=START + datetime.timedelta(days=30, hours=1))
    closed = make_case(lawyer.profile, starts_at=START, ends_at=START + datetime.timedelta(hours=1))
    Case.objects.filter(pk=closed.pk).close(Case.ClosedBecause.LAWYER)

    for case in [elsewhere, closed]:
        assert lawyer.send(clip, case=case).status_code == 404
    assert not Message.objects.exists()
    assert not AuditEntry.objects.filter(action=Action.MESSAGE).exists()


def test_the_lawyer_reads_and_answers_their_threads_with_no_open_case(lawyer, clip):
    lawyer.send(clip)
    vault.send_message(clip, lawyer.profile, Message.Direction.FROM_CODE_HOLDER, 'They pushed first.')
    Case.objects.filter(pk=lawyer.case.pk).close(Case.ClosedBecause.LAWYER)
    entries = AuditEntry.objects.count()

    page = lawyer.page()

    assert page.status_code == 200
    assert shown_texts(page, lawyer.private_key) == [TEXT, 'They pushed first.']
    # Reading is not logged.
    assert AuditEntry.objects.count() == entries

    assert lawyer.reply(clip.pk).status_code == 302
    assert [text for _message, text in vault.code_holder_messages(clip, TEST_CODE)][-1] == 'And afterwards?'
    reply = AuditEntry.objects.get(action=Action.REPLY)
    assert (reply.lawyer_profile, reply.case, reply.clip_id) == (lawyer.profile, None, clip.pk)


def test_the_messages_page_shows_each_threads_clip_by_identifier_only(lawyer, clip):
    lawyer.send(clip)

    content = lawyer.page().content.decode()

    assert str(clip.pk) in content
    assert 'Barricade' not in content
    for name in ['clip', 'preview', 'download']:
        assert reverse(f'lawyer:{name}', kwargs={'case_id': lawyer.case.pk, 'clip_id': clip.pk}) not in content


def test_no_other_lawyers_threads_and_no_reply_without_a_thread(lawyer, clip):
    other = Lawyer('other@example.org')
    other.send(clip, text='A message of my own')

    assert str(clip.pk) not in lawyer.page().content.decode()
    assert lawyer.reply(clip.pk).status_code == 404
    assert Message.objects.filter(lawyer_profile=lawyer.profile).count() == 0


def test_the_lawyer_chooses_per_message_whether_to_be_named(lawyer, clip):
    lawyer.send(clip, identified='on')
    lawyer.reply(clip.pk)

    assert list(Message.objects.order_by('position').values_list('identified', flat=True)) == [True, False]


def test_a_lawyer_whose_key_the_admin_has_not_confirmed_sends_nothing(clip):
    lawyer = Lawyer('reset@example.org', confirmed=False)

    response = lawyer.send(clip)

    assert response.status_code == 302
    assert not Message.objects.exists()
    assert not AuditEntry.objects.filter(action=Action.MESSAGE).exists()


def test_no_lawyer_learns_whether_the_code_holder_has_read_a_message(lawyer, clip):
    lawyer.send(clip)

    def page():
        # Without the CSRF tokens, which change on every page.
        return re.sub(r'[A-Za-z0-9]{64}', '', lawyer.page().content.decode())

    page()  # Shows, and so clears, the notice that the message was sent.
    before = page()
    Message.objects.update(seen=True)

    assert page() == before


def test_a_code_holders_message_is_new_until_the_lawyer_opens_their_messages(lawyer, clip):
    lawyer.send(clip)
    vault.send_message(clip, lawyer.profile, Message.Direction.FROM_CODE_HOLDER, 'They pushed first.')

    assert '<strong>New</strong>' in lawyer.page().content.decode()
    assert '<strong>New</strong>' not in lawyer.page().content.decode()


def test_each_threads_form_has_its_own_fields(lawyer, clip):
    other_clip = Clip.objects.create(
        code_hash=upload_codes.keyed_hash('another code'), status=Clip.Status.READY,
        stated_time=START + datetime.timedelta(minutes=20), stated_cells=[CENTRE],
        as_uploaded_stated_time=START, as_uploaded_stated_cells=[CENTRE],
    )
    with_code_holder_key(other_clip, code='ANOTHERCODE')
    lawyer.send(clip)
    lawyer.send(other_clip)

    ids = re.findall(r' id="([^"]+)"', lawyer.page().content.decode())

    # A label for one thread's "name me" box never ticks another's.
    assert len([found for found in ids if found.endswith('-identified')]) == 2
    assert len(ids) == len(set(ids))
