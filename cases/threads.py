"""A lawyer's messages to the code holders of clips (SPEC.md §7): the first message of a thread is
sent from the clip's page, through one of the lawyer's usable cases that the clip matches; the
lawyer reads and answers their threads on their messages page, which needs no case, so that
closing a case does not cut a code holder's answer off. Sending is logged, reading is not.

The server encrypts each text as it arrives (clips.vault.send_message); the lawyer's browser
opens them with their personal key (keys/static/keys/messages.js).
"""

import json

from django.contrib import messages as notices
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_GET, require_POST

from accounts.roles import lawyer_required
from audit.models import Action, record
from clips import vault
from keys import grants
from clips.models import Message
from clips.threads import deletion_time
from keys.views import b64

from .clip_pages import case_and_clip
from .forms import MessageForm, WrongReportForm


@sensitive_variables('text')
def _send(request, clip, action, text, identified, case=None, kind=Message.Kind.MESSAGE):
    """Send `text` in the lawyer's thread on the clip, and log it as `action`: a message or report
    names the case it was sent through, a reply none. False, with a notice, if it cannot be sent
    now."""
    profile = request.user.lawyer_profile
    try:
        with transaction.atomic():
            message = vault.send_message(
                clip, profile, Message.Direction.TO_CODE_HOLDER, text, kind=kind, identified=identified,
            )
            record(action, profile, case=case, clip=clip)
    except vault.NotALawyer:
        # Suspended since the request began.
        raise Http404 from None
    except vault.NoLawyerKey:
        notices.error(request, _('You can send messages once the admin has confirmed your personal key.'))
        return False
    except vault.NoCodeHolderKey:
        notices.error(request, _("This clip's code holder cannot receive messages yet."))
        return False
    return message


def thread_url(clip_id):
    return f"{reverse('lawyer:messages')}#thread-{clip_id}"


def _form_errors(request, form):
    notices.error(request, ' '.join(error for errors in form.errors.values() for error in errors))


@lawyer_required
@require_POST
@sensitive_post_parameters('text')
def send(request, case_id, clip_id):
    case, clip, _match = case_and_clip(request, case_id, clip_id)
    form = MessageForm(request.POST)
    if not form.is_valid():
        _form_errors(request, form)
        return redirect('lawyer:clip', case_id=case.pk, clip_id=clip.pk)
    if not _send(request, clip, Action.MESSAGE, form.cleaned_data['text'], form.cleaned_data['identified'], case):
        return redirect('lawyer:clip', case_id=case.pk, clip_id=clip.pk)
    notices.success(request, _('Your message is sent. Its thread is on your messages page.'))
    return redirect(thread_url(clip.pk))


WRONG_REPORT_ACTIONS = {Message.Kind.WRONG_TIME: Action.REPORT_WRONG_TIME, Message.Kind.WRONG_PLACE: Action.REPORT_WRONG_PLACE}


@lawyer_required
@require_POST
@sensitive_post_parameters('note')
def report_wrong(request, case_id, clip_id):
    """A report that the clip's time or place is wrong: a message of that kind in the lawyer's
    thread, logged once, as the report. It cannot be cancelled, since the code holder has it
    already: a correction is another message (SPEC.md §7)."""
    case, clip, _match = case_and_clip(request, case_id, clip_id)
    form = WrongReportForm(request.POST)
    if not form.is_valid():
        _form_errors(request, form)
        return redirect('lawyer:clip', case_id=case.pk, clip_id=clip.pk)
    kind = Message.Kind(form.cleaned_data['kind'])
    if not _send(request, clip, WRONG_REPORT_ACTIONS[kind], form.cleaned_data['note'], form.cleaned_data['identified'], case, kind):
        return redirect('lawyer:clip', case_id=case.pk, clip_id=clip.pk)
    notices.success(request, _('Your report is sent to the code holder. Its thread is on your messages page.'))
    return redirect(thread_url(clip.pk))


def message_json(message):
    """What the browser needs to open a message's text, as JSON in an attribute; '' if it has none
    the lawyer can open. The browser rebuilds the additional data from what the page shows."""
    sealed = vault.sealed_message(message)
    if sealed is None:
        return ''
    return json.dumps({'ciphertext': b64(sealed['ciphertext']), 'sealed_key': b64(sealed['sealed_key'])})


@lawyer_required
@require_GET
def messages_page(request):
    profile = request.user.lawyer_profile
    threads = {}
    for message in Message.objects.filter(lawyer_profile=profile).select_related('lawyer_key').order_by('clip_id', 'position'):
        threads.setdefault(message.clip_id, []).append({
            'message': message, 'sealed': message_json(message),
            'new': message.direction == Message.Direction.FROM_CODE_HOLDER and not message.seen,
        })
    # The latest thread first; positions order a thread, times only to their 4-hour block.
    ordered = sorted(threads.items(), key=lambda thread: thread[1][-1]['message'].sent_at, reverse=True)
    # Opened now: the "new" marks above are the last, and a notification may be sent again. Only
    # those shown, not one that arrived since they were read.
    Message.objects.filter(
        pk__in=[item['message'].pk for thread in threads.values() for item in thread if item['new']],
    ).update(seen=True)
    return render(request, 'cases/messages.html', {
        'threads': [
            # Its own field ids, so that a label never ticks another thread's box.
            {
                'clip_id': clip_id, 'messages': thread, 'form': MessageForm(auto_id=f'thread-{clip_id}-%s'),
                'deleted_on': deletion_time(max(item['message'].sent_at for item in thread)),
            }
            for clip_id, thread in ordered
        ],
    })


@lawyer_required
@require_POST
@sensitive_post_parameters('text')
def reply(request, clip_id):
    """A message in a thread the lawyer already has, whatever has become of their cases: also on a
    withdrawn clip, until the thread expires or the clip is deleted (SPEC.md §7)."""
    form = MessageForm(request.POST)
    with transaction.atomic():
        # Under the lock that deleting expired threads takes, so that the thread is still there
        # when the reply joins it, rather than the reply starting a thread of its own.
        grants.lock()
        first = Message.objects.filter(lawyer_profile=request.user.lawyer_profile, clip_id=clip_id).select_related('clip').first()
        if first is None:
            raise Http404
        if not form.is_valid():
            _form_errors(request, form)
        elif _send(request, first.clip, Action.REPLY, form.cleaned_data['text'], form.cleaned_data['identified']):
            notices.success(request, _('Your message is sent.'))
    return redirect(thread_url(clip_id))
