"""Email notifications for lawyers (SPEC.md §7): at first only of a new message from a code holder.

A lawyer with notifications on gets one email when something starts waiting for them, at the end
of the 4-hour block it arrived in if it is still unopened then; then no other until they open
their messages page, which clears LawyerProfile.notified_before. The email says only that
something is waiting, with a link to log in: never what, nor about which clip. It is in French
and English, since the archive does not know which the lawyer reads.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Exists, OuterRef
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from accounts.models import LawyerProfile
from clips.models import Message
from clips.time_blocks import four_hour_block

logger = logging.getLogger(__name__)


def _email():
    # The login page: it says nothing about what is waiting.
    context = {'link': settings.ARCHIVE_URL + reverse('accounts:login')}
    subjects, bodies = [], []
    for language in ('fr', 'en'):
        with translation.override(language):
            subjects.append(_('Something is waiting for you in the footage archive'))
            bodies.append(render_to_string('cases/notification_email.txt', context))
    return ' / '.join(subjects), '\n\n'.join(bodies)


def _unopened(before):
    """A code holder's unopened message to the lawyer of the outer query, sent before `before`."""
    return Message.objects.filter(
        lawyer_profile=OuterRef('pk'), direction=Message.Direction.FROM_CODE_HOLDER, seen=False, sent_at__lt=before,
    )


def send_notifications(now=None):
    """Email each lawyer who is due a notification; returns how many were sent."""
    # Messages from a 4-hour block that has ended.
    cutoff = four_hour_block(now or timezone.now())
    # Due again once none of the messages last emailed about is left unopened.
    LawyerProfile.objects.filter(notified_before__isnull=False).exclude(
        Exists(_unopened(OuterRef('notified_before'))),
    ).update(notified_before=None)
    due = LawyerProfile.objects.filter(status=LawyerProfile.Status.LAWYER, notifications=True, notified_before__isnull=True)
    subject, body = _email()
    sent = 0
    for profile in due.filter(Exists(_unopened(cutoff))).select_related('user'):
        # Claimed first, and only while something still waits, so that two runs at once never
        # email a lawyer twice, and a lawyer who opened their messages meanwhile not at all.
        if not due.filter(Exists(_unopened(cutoff)), pk=profile.pk).update(notified_before=cutoff):
            continue
        try:
            send_mail(subject, body, None, [profile.user.email])
        except Exception:
            # Tried again at the next run. Never which lawyer, nor the error, which may name
            # them: the count is enough.
            LawyerProfile.objects.filter(pk=profile.pk).update(notified_before=None)
            logger.warning('A notification email could not be sent; it will be tried again.')
            continue
        sent += 1
    return sent
