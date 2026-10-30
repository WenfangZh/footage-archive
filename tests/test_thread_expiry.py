"""Thread expiry (SPEC.md §7, M6.2): a thread's messages are deleted the set time after its last
message, never before; each page says when; the audit log keeps its entries, and no letter is
given again to another lawyer on the clip."""

import datetime

import pytest
from django.test import Client
from django.urls import reverse

from audit.models import Action, AuditEntry, record
from clips import threads, vault
from clips.models import Message
from tests.conftest import make_case
from tests.test_code_holder_messages import ENGLISH, clip, page, section  # noqa: F401 (the clip fixture)
from tests.test_lawyer_messages import Lawyer
from tests.test_vault_messages import FROM, TO, make_lawyer

pytestmark = pytest.mark.django_db

# The start of a 4-hour block: 08h in Paris.
SENT = datetime.datetime(2026, 1, 1, 7, tzinfo=datetime.UTC)
DAY = datetime.timedelta(days=1)


@pytest.fixture(autouse=True)
def ten_days(settings):
    settings.THREAD_LIFETIME_DAYS = 10


def send(clip, profile, direction, text, at=SENT):  # noqa: F811
    message = vault.send_message(clip, profile, direction, text)
    Message.objects.filter(pk=message.pk).update(sent_at=at)
    return message


def test_a_thread_is_deleted_the_set_time_after_its_last_message_and_not_before(clip):  # noqa: F811
    quiet, _ = make_lawyer('quiet@example.org')
    answered, _ = make_lawyer('answered@example.org')
    send(clip, quiet, TO, 'Were you near the station?')
    send(clip, answered, TO, 'Did you keep filming?')
    send(clip, answered, FROM, 'Yes.', at=SENT + 5 * DAY)
    record(Action.MESSAGE, quiet, case=make_case(quiet), clip=clip)

    # The end of the last message's block, plus ten days.
    due = SENT + datetime.timedelta(hours=4) + 10 * DAY
    assert threads.delete_expired_threads(now=due - datetime.timedelta(minutes=1)) == 0
    assert threads.delete_expired_threads(now=due) == 1

    assert not Message.objects.filter(lawyer_profile=quiet).exists()
    assert Message.objects.filter(lawyer_profile=answered).count() == 2
    assert AuditEntry.objects.filter(action=Action.MESSAGE, lawyer_profile=quiet).exists()


def test_a_thread_whose_last_block_is_the_long_one_when_the_clocks_go_back_is_not_deleted_early(clip):  # noqa: F811
    profile, _ = make_lawyer('autumn@example.org')
    # The 00h block of 25 October 2026 in Paris lasts five hours, to 04h winter time (03h UTC).
    send(clip, profile, TO, 'Sent at 02h30 UTC, in that block', at=datetime.datetime(2026, 10, 24, 22, tzinfo=datetime.UTC))

    due = datetime.datetime(2026, 11, 4, 3, tzinfo=datetime.UTC)
    assert threads.delete_expired_threads(now=due - datetime.timedelta(minutes=1)) == 0
    assert threads.delete_expired_threads(now=due) == 1


def test_each_page_says_when_its_thread_is_deleted(clip):  # noqa: F811
    lawyer = Lawyer('reader@example.org')
    # The 20h block of 20 October 2026 in Paris ends at midnight; ten calendar days later the
    # clocks have gone back, and midnight is an hour later in UTC.
    send(clip, lawyer.profile, TO, 'Were you near the station?', at=datetime.datetime(2026, 10, 20, 18, tzinfo=datetime.UTC))

    lawyers_page = lawyer.client.get(reverse('lawyer:messages'), headers=ENGLISH).content.decode()
    code_holders_page = page(Client())

    # Ten days after the end of that block.
    assert 'This thread will be deleted on 31 October 2026' in lawyers_page
    assert 'Save what you need into your own records.' in lawyers_page
    assert 'These messages will be deleted on 31 October 2026' in section(code_holders_page, 'A')


def test_no_letter_is_given_again_to_another_lawyer_once_a_thread_is_deleted(clip):  # noqa: F811
    first, _ = make_lawyer('first@example.org')
    send(clip, first, TO, 'The first thread')
    page(Client())
    threads.delete_expired_threads(now=SENT + 30 * DAY)

    second, _ = make_lawyer('second@example.org')
    vault.send_message(clip, second, TO, 'The second thread')
    content = page(Client())

    assert 'The second thread' in section(content, 'B') and '<h3>Lawyer A</h3>' not in content
    vault.send_message(clip, first, TO, 'The first lawyer again')
    assert 'The first lawyer again' in section(page(Client()), 'A')

