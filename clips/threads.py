"""A clip's messages as its code holder sees them (SPEC.md §7): one thread per lawyer, told apart
as lawyer A, B and so on, each sender "a verified defence lawyer" unless they chose to be named,
and the code holder's replies. Opened with the upload code, during the request only.
"""

import datetime

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from accounts.models import LawyerProfile
from keys import grants
from keys.models import LawyerKey

from . import vault
from .models import Clip, Message, ThreadLetter
from .time_blocks import BLOCK_HOURS


def letter(number):
    """A, B, …, Z, then AA, AB, …: the name of a thread's lawyer, from its number (0 for A)."""
    name = ''
    number += 1
    while number:
        number, remainder = divmod(number - 1, 26)
        name = chr(ord('A') + remainder) + name
    return name


def _numbers(clip, threads):
    """Each thread's number, by lawyer id: kept from the code holder's earlier visits, and given
    now to new threads in the order they began (their first 4-hour block, then their lawyer). None
    if the clip was deleted meanwhile."""
    with transaction.atomic():
        # Locked, so that two visits at once never give two threads the same letter; without the
        # key lock, which a lawyer's message being saved takes, so that the two never deadlock.
        if not Clip.objects.select_for_update(no_key=True).filter(pk=clip.pk).exists():
            return None
        given = dict(ThreadLetter.objects.filter(clip=clip).values_list('lawyer_profile_id', 'number'))
        new = sorted(
            (profile_id for profile_id in threads if profile_id not in given),
            key=lambda profile_id: (min(item['message'].sent_at for item in threads[profile_id]), profile_id),
        )
        following = max(given.values(), default=-1) + 1
        ThreadLetter.objects.bulk_create([
            ThreadLetter(clip=clip, lawyer_profile_id=profile_id, number=following + index)
            for index, profile_id in enumerate(new)
        ])
        given.update({profile_id: following + index for index, profile_id in enumerate(new)})
    return given


@sensitive_variables('code', 'opened')
def code_holder_threads(clip, code):
    """The clip's threads, by letter, with each message's text opened with the normalised upload
    code `code`; then the messages shown count as seen by the code holder."""
    opened = vault.code_holder_messages(clip, code)
    if not opened:
        return []
    profiles = LawyerProfile.objects.select_related('user').in_bulk({message.lawyer_profile_id for message, _ in opened})
    threads = {}
    for message, text in opened:
        profile = profiles[message.lawyer_profile_id]
        threads.setdefault(message.lawyer_profile_id, []).append({
            'message': message, 'text': text,
            'new': message.direction == Message.Direction.TO_CODE_HOLDER and not message.seen,
            # Only on a message the lawyer chose to be named on.
            'named': f'{profile.user.first_name} {profile.user.last_name}, {profile.bar}' if message.identified else '',
        })
    numbers = _numbers(clip, threads)
    if numbers is None:
        return []
    # As clips.vault.send_message requires: an admitted, unsuspended lawyer whose personal key the
    # admin confirmed.
    confirmed = set(LawyerKey.objects.current().filter(
        lawyer_profile_id__in=threads, admin_confirmed_at__isnull=False,
    ).values_list('lawyer_profile_id', flat=True))
    # Only those shown, not one that arrived since they were read.
    Message.objects.filter(
        pk__in=[item['message'].pk for thread in threads.values() for item in thread if item['new']],
    ).update(seen=True)
    shown = []
    for profile_id in sorted(threads, key=numbers.get):
        status = profiles[profile_id].status
        shown.append({
            'letter': letter(numbers[profile_id]), 'messages': threads[profile_id],
            'deleted_on': deletion_time(max(item['message'].sent_at for item in threads[profile_id])),
            # The first message's id names the thread in a reply, rather than the lawyer's.
            'reference': threads[profile_id][0]['message'].pk,
            'no_longer_has_access': status != LawyerProfile.Status.LAWYER,
            'answerable': status == LawyerProfile.Status.LAWYER and profile_id in confirmed,
        })
    return shown


def deletion_time(last_sent_at):
    """When a thread whose last message was sent in the 4-hour block starting `last_sent_at` is
    deleted (SPEC.md §7): the set time after the end of that block, so never early."""
    return last_sent_at + datetime.timedelta(hours=BLOCK_HOURS, days=settings.THREAD_LIFETIME_DAYS)


def delete_expired_threads(now=None):
    """Delete the messages of every thread past its deletion time; returns how many threads. The
    audit entries stay, and so do the threads' letters, so that no letter is given to another
    lawyer on the clip."""
    now = now or timezone.now()
    with transaction.atomic():
        # The lock sending a message takes, so that none joins a thread as it goes.
        grants.lock()
        expired = [
            (thread['clip_id'], thread['lawyer_profile_id'])
            for thread in Message.objects.values('clip_id', 'lawyer_profile_id').annotate(last=Max('sent_at'))
            if deletion_time(thread['last']) <= now
        ]
        for clip_id, profile_id in expired:
            Message.objects.filter(clip_id=clip_id, lawyer_profile_id=profile_id).delete()
    return len(expired)
