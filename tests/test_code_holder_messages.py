"""A clip's messages on its code holder's page (SPEC.md §7, M6.2): opened with the upload code,
each lawyer told apart by a letter and shown as a verified defence lawyer unless they chose to be
named, new ones marked once, and replies that reach only a lawyer who can still receive them."""

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import LawyerProfile
from clips import upload_codes, vault
from clips.models import Clip, Message
from tests.conftest import TEST_CODE, with_code_holder_key
from tests.test_vault_messages import FROM, TO, make_lawyer, opened_by_lawyer

pytestmark = pytest.mark.django_db

ENGLISH = {'accept-language': 'en'}
PAGE_URL = reverse('clips:code_holder')
REPLY_URL = reverse('clips:reply')


@pytest.fixture
def clip():
    return with_code_holder_key(Clip.objects.create(code_hash=upload_codes.keyed_hash(TEST_CODE), status=Clip.Status.READY))


def named_lawyer(email, first_name):
    profile, private_key = make_lawyer(email)
    profile.user.first_name, profile.user.last_name = first_name, 'Exemple'
    profile.user.save()
    return profile, private_key


def page(client):
    return client.post(PAGE_URL, {'code': TEST_CODE}, headers=ENGLISH).content.decode()


def section(content, letter):
    start = content.index(f'<h3>Lawyer {letter}</h3>')
    return content[start:content.index('</section>', start)]


def reply(client, thread, text='Yes, from the corner.'):
    return client.post(REPLY_URL, {'code': TEST_CODE, 'thread': thread.pk, 'text': text}, headers=ENGLISH)


def test_the_code_holder_reads_each_lawyers_thread_as_a_verified_defence_lawyer_unless_named(client, clip):
    camille, _ = named_lawyer('camille@example.org', 'Camille')
    dominique, _ = named_lawyer('dominique@example.org', 'Dominique')
    vault.send_message(clip, camille, TO, 'Were you near the station?')
    vault.send_message(clip, dominique, TO, 'Did you keep filming?\nAfter the charge?', identified=True)

    content = page(client)

    first, second = section(content, 'A'), section(content, 'B')
    assert 'Were you near the station?' in first and 'A verified defence lawyer' in first and 'Camille' not in content
    assert 'Did you keep filming?<br>After the charge?' in second and 'Dominique Exemple, Paris' in second


def test_a_message_is_marked_new_until_the_code_holder_has_been_shown_it(client, clip):
    profile, _ = make_lawyer('lawyer@example.org')
    vault.send_message(clip, profile, TO, 'Were you near the station?')

    assert '<strong>New</strong>' in page(client)
    assert '<strong>New</strong>' not in page(client)


def test_the_code_holder_replies_and_the_lawyer_reads_it_with_their_key(client, clip):
    profile, private_key = make_lawyer('lawyer@example.org')
    first = vault.send_message(clip, profile, TO, 'Were you near the station?')

    response = reply(client, first)

    assert response.status_code == 200
    answer = Message.objects.get(direction=FROM)
    assert (answer.clip, answer.lawyer_profile, answer.position, answer.identified) == (clip, profile, 1, False)
    assert opened_by_lawyer(answer, private_key) == 'Yes, from the corner.'
    assert 'Your reply is sent.' in response.content.decode()


@pytest.mark.parametrize('status', [LawyerProfile.Status.SUSPENDED, LawyerProfile.Status.REVOKED])
def test_a_lawyer_who_no_longer_has_access_is_marked_and_cannot_be_answered(client, clip, status):
    profile, _ = make_lawyer('lawyer@example.org')
    first = vault.send_message(clip, profile, TO, 'Were you near the station?')
    profile.change_status(status)

    thread = section(page(client), 'A')
    response = reply(client, first, text='My answer, to keep.')

    assert 'This lawyer no longer has access.' in thread and REPLY_URL not in thread
    assert response.status_code == 409
    assert 'My answer, to keep.' in response.content.decode()
    assert not Message.objects.filter(direction=FROM).exists()


def test_a_lawyer_whose_new_key_is_unconfirmed_cannot_be_answered_for_now_without_saying_why(client, clip):
    profile, _ = make_lawyer('lawyer@example.org')
    first = vault.send_message(clip, profile, TO, 'Were you near the station?')
    # A key reset: the old key retired, a new one waiting for the admin's confirmation.
    from keys.models import LawyerKey
    from tests.conftest import make_key
    LawyerKey.objects.filter(lawyer_profile=profile).update(retired_at=timezone.now())
    make_key(profile)

    thread = section(page(client), 'A')

    assert 'You cannot answer this lawyer for now.' in thread
    assert 'no longer has access' not in thread and REPLY_URL not in thread
    assert reply(client, first).status_code == 409
    assert not Message.objects.filter(direction=FROM).exists()


def test_a_reply_names_a_thread_of_this_clip_only(client, clip):
    other_clip = with_code_holder_key(Clip.objects.create(code_hash=upload_codes.keyed_hash('another code'), status=Clip.Status.READY), code='OTHERCODE')
    profile, _ = make_lawyer('lawyer@example.org')
    elsewhere = vault.send_message(other_clip, profile, TO, 'About another clip')

    assert reply(client, elsewhere).status_code == 400
    assert not Message.objects.filter(direction=FROM).exists()


def test_no_messages_without_the_right_code(client, clip):
    profile, _ = make_lawyer('lawyer@example.org')
    vault.send_message(clip, profile, TO, 'Were you near the station?')

    response = client.post(PAGE_URL, {'code': 'WRONGCODE'}, headers=ENGLISH)

    assert response.status_code == 404
    assert 'station' not in response.content.decode()
