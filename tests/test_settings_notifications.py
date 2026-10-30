"""A lawyer's settings and email notifications (SPEC.md §7, M6.2): the settings page sets the
default margin, identification and notifications; a lawyer with notifications on gets one email
when something starts waiting, at the end of the 4-hour block if it is still unopened then, saying
nothing more, and no other until they open their messages; with notifications off, none."""

import datetime
import re

import pytest
from django.urls import reverse

from accounts.models import LawyerProfile
from cases import notifications
from clips import threads, vault
from clips.models import Message
from tests.test_lawyer_messages import ENGLISH, Lawyer, clip, lawyer  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db

# The 16h block of 15 June 2026 in Paris (14h to 18h UTC), and a moment in the next one.
BLOCK = datetime.datetime(2026, 6, 15, 14, tzinfo=datetime.UTC)
SAME_BLOCK = BLOCK + datetime.timedelta(hours=3, minutes=59)
NEXT_BLOCK = BLOCK + datetime.timedelta(hours=4)
FROM = Message.Direction.FROM_CODE_HOLDER


def save_settings(lawyer, **fields):  # noqa: F811
    values = {'default_margin_minutes': '5', 'default_margin_rings': '0', **fields}
    return lawyer.client.post(reverse('lawyer:settings'), values, headers=ENGLISH)


def arrives(clip, lawyer, at=BLOCK, text='They pushed first.'):  # noqa: F811
    message = vault.send_message(clip, lawyer.profile, FROM, text)
    Message.objects.filter(pk=message.pk).update(sent_at=at)


def name_boxes(content):
    """Whether each "name me" box on a page is ticked."""
    return ['checked' in tag for tag in re.findall(r'<input[^>]*name="identified"[^>]*>', content)]


def test_the_settings_page_sets_the_margin_identification_and_notifications(lawyer):  # noqa: F811
    response = save_settings(lawyer, identified_by_default='on')

    assert response.status_code == 302
    lawyer.profile.refresh_from_db()
    assert (lawyer.profile.default_margin_minutes, lawyer.profile.default_margin_rings) == (5, 0)
    assert lawyer.profile.identified_by_default and not lawyer.profile.notifications


def test_the_old_margin_address_leads_to_the_settings_page(lawyer):  # noqa: F811
    response = lawyer.client.get(reverse('lawyer:default_margin'))

    assert response.status_code == 301 and response.url == reverse('lawyer:settings')


def test_the_identification_setting_ticks_each_name_me_box_by_default(lawyer, clip):  # noqa: F811
    clip_url = reverse('lawyer:clip', kwargs={'case_id': lawyer.case.pk, 'clip_id': clip.pk})
    lawyer.send(clip)
    assert name_boxes(lawyer.client.get(clip_url).content.decode()) == [False, False]

    save_settings(lawyer, identified_by_default='on', notifications='on')

    assert name_boxes(lawyer.client.get(clip_url).content.decode()) == [True, True]
    assert name_boxes(lawyer.page().content.decode()) == [True]


def test_one_email_at_the_end_of_the_block_saying_only_that_something_is_waiting(lawyer, clip, mailoutbox):  # noqa: F811
    lawyer.send(clip)
    arrives(clip, lawyer)

    assert notifications.send_notifications(now=SAME_BLOCK) == 0
    assert notifications.send_notifications(now=NEXT_BLOCK) == 1

    [email] = mailoutbox
    assert email.to == [lawyer.profile.user.email]
    assert 'Something is waiting for you' in email.body and "Quelque chose vous attend" in email.body
    assert str(clip.pk) not in email.subject + email.body and 'pushed' not in email.body
    # A link to log in, which says nothing more either.
    assert 'https://archive.example.org/login/' in email.body


def test_no_other_email_until_the_lawyer_opens_their_messages(lawyer, clip, mailoutbox):  # noqa: F811
    lawyer.send(clip)
    arrives(clip, lawyer)
    notifications.send_notifications(now=NEXT_BLOCK)
    arrives(clip, lawyer, at=NEXT_BLOCK, text='And then they ran.')

    assert notifications.send_notifications(now=NEXT_BLOCK + datetime.timedelta(hours=4)) == 0
    lawyer.page()
    arrives(clip, lawyer, at=NEXT_BLOCK + datetime.timedelta(hours=8), text='One more thing.')
    assert notifications.send_notifications(now=NEXT_BLOCK + datetime.timedelta(hours=12)) == 1
    assert len(mailoutbox) == 2


def test_nothing_is_sent_for_what_the_lawyer_opened_before_the_block_ended(lawyer, clip, mailoutbox):  # noqa: F811
    lawyer.send(clip)
    arrives(clip, lawyer)
    lawyer.page()

    assert notifications.send_notifications(now=NEXT_BLOCK) == 0
    assert not mailoutbox


def test_a_lawyer_with_notifications_off_or_no_longer_admitted_gets_none(lawyer, clip, mailoutbox):  # noqa: F811
    other = Lawyer('other@example.org')
    for each in (lawyer, other):
        each.send(clip)
        arrives(clip, each)
    save_settings(lawyer)
    other.profile.change_status(LawyerProfile.Status.SUSPENDED)

    assert notifications.send_notifications(now=NEXT_BLOCK) == 0
    assert not mailoutbox


@pytest.mark.parametrize('error', [OSError('The mail server is down.'), ValueError('A header the backend refuses.')])
def test_an_email_that_could_not_be_sent_is_tried_again(lawyer, clip, mailoutbox, monkeypatch, error):  # noqa: F811
    lawyer.send(clip)
    arrives(clip, lawyer)

    def refuse(*args, **kwargs):
        raise error

    monkeypatch.setattr(notifications, 'send_mail', refuse)
    assert notifications.send_notifications(now=NEXT_BLOCK) == 0
    monkeypatch.undo()
    assert notifications.send_notifications(now=NEXT_BLOCK) == 1


def test_a_lawyer_whose_unopened_thread_was_deleted_is_told_of_the_next_message(lawyer, clip, mailoutbox, settings):  # noqa: F811
    settings.THREAD_LIFETIME_DAYS = 1
    lawyer.send(clip)
    Message.objects.update(sent_at=BLOCK)
    arrives(clip, lawyer)
    notifications.send_notifications(now=NEXT_BLOCK)
    threads.delete_expired_threads(now=NEXT_BLOCK + datetime.timedelta(days=2))

    lawyer.send(clip)
    arrives(clip, lawyer, at=NEXT_BLOCK + datetime.timedelta(days=2))

    assert notifications.send_notifications(now=NEXT_BLOCK + datetime.timedelta(days=3)) == 1
    assert len(mailoutbox) == 2
