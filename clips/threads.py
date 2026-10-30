"""A clip's messages as its code holder sees them (SPEC.md §7): one thread per lawyer, told apart
as lawyer A, B and so on, each sender "a verified defence lawyer" unless they chose to be named,
and the code holder's replies. Opened with the upload code, during the request only.
"""

from django.db import transaction
from django.views.decorators.debug import sensitive_variables

from accounts.models import LawyerProfile
from keys.models import LawyerKey

from . import vault
from .models import Clip, Message, ThreadLetter


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
    now to new threads in the order they began (their first 4-hour block, then their lawyer)."""
    with transaction.atomic():
        # Locked, so that two visits at once never give two threads the same letter.
        if not Clip.objects.select_for_update().filter(pk=clip.pk).exists():
            return {}
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
    for profile_id in sorted(threads, key=lambda profile_id: numbers.get(profile_id, 0)):
        status = profiles[profile_id].status
        shown.append({
            'letter': letter(numbers.get(profile_id, 0)), 'messages': threads[profile_id],
            # The first message's id names the thread in a reply, rather than the lawyer's.
            'reference': threads[profile_id][0]['message'].pk,
            'no_longer_has_access': status != LawyerProfile.Status.LAWYER,
            'answerable': status == LawyerProfile.Status.LAWYER and profile_id in confirmed,
        })
    return shown
