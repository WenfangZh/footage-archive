"""A clip's messages as its code holder sees them (SPEC.md §7): one thread per lawyer, told apart
as lawyer A, B and so on, each sender "a verified defence lawyer" unless they chose to be named,
and the code holder's replies. Opened with the upload code, during the request only.
"""

from django.db.models import Min
from django.views.decorators.debug import sensitive_variables

from accounts.models import LawyerProfile
from keys.grants import current_key

from . import vault
from .models import Message


def letter(index):
    """A, B, …, Z, then AA, AB, …: the name a thread's lawyer goes by on the code holder's page."""
    name = ''
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(ord('A') + remainder) + name
    return name


def answerable(profile):
    """Whether the code holder can answer this lawyer now: an admitted, unsuspended lawyer holding a
    personal key the admin confirmed, as clips.vault.send_message requires."""
    if profile.status != LawyerProfile.Status.LAWYER:
        return False
    key = current_key(profile)
    return key is not None and key.admin_confirmed_at is not None


@sensitive_variables('code', 'opened')
def code_holder_threads(clip, code):
    """The clip's threads, oldest first, with each message's text opened with the normalised
    upload code `code`; then every message so far counts as shown to the code holder.

    Letters follow each thread's first message, so that a new thread never renames an older one;
    threads begun in the same 4-hour block go by their lawyer's id."""
    opened = vault.code_holder_messages(clip, code)
    if not opened:
        return []
    started = dict(
        Message.objects.filter(clip=clip).values('lawyer_profile_id').annotate(first=Min('sent_at')).values_list(
            'lawyer_profile_id', 'first',
        ),
    )
    profiles = LawyerProfile.objects.select_related('user').in_bulk(started)
    threads = {}
    for message, text in opened:
        profile = profiles[message.lawyer_profile_id]
        threads.setdefault(message.lawyer_profile_id, []).append({
            'message': message, 'text': text,
            'new': message.direction == Message.Direction.TO_CODE_HOLDER and not message.seen,
            # Only on a message the lawyer chose to be named on.
            'named': f'{profile.user.first_name} {profile.user.last_name}, {profile.bar}' if message.identified else '',
        })
    ordered = sorted(threads, key=lambda profile_id: (started[profile_id], profile_id))
    Message.objects.filter(clip=clip, direction=Message.Direction.TO_CODE_HOLDER, seen=False).update(seen=True)
    shown = []
    for index, profile_id in enumerate(ordered):
        profile = profiles[profile_id]
        shown.append({
            'letter': letter(index), 'messages': threads[profile_id],
            # The first message's id names the thread in a reply, rather than the lawyer's.
            'reference': threads[profile_id][0]['message'].pk,
            'no_longer_has_access': profile.status != LawyerProfile.Status.LAWYER,
            'answerable': answerable(profile),
        })
    return shown
