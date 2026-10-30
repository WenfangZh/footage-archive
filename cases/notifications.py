"""Email notifications for lawyers (SPEC.md §7): at first only of a new message from a code holder.

A lawyer with notifications on gets one email when something starts waiting for them, at the end
of the 4-hour block it arrived in if it is still unopened then; then no other until they open
their messages page, which clears LawyerProfile.notified. The email says only that something is
waiting: never what, nor about which clip. It is in French and English, since the archive does
not know which the lawyer reads.
"""

import logging

from django.core.mail import send_mail
from django.db.models import Exists, OuterRef
from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from accounts.models import LawyerProfile
from clips.models import Message
from clips.time_blocks import four_hour_block

logger = logging.getLogger(__name__)


def _email():
    subjects, bodies = [], []
    for language in ('fr', 'en'):
        with translation.override(language):
            subjects.append(_('Something is waiting for you in the footage archive'))
            bodies.append(render_to_string('cases/notification_email.txt'))
    return ' / '.join(subjects), '\n\n'.join(bodies)


def send_notifications(now=None):
    """Email each lawyer who is due a notification; returns how many were sent."""
    waiting = Message.objects.filter(
        lawyer_profile=OuterRef('pk'), direction=Message.Direction.FROM_CODE_HOLDER, seen=False,
        # Arrived in a 4-hour block that has ended.
        sent_at__lt=four_hour_block(now or timezone.now()),
    )
    due = LawyerProfile.objects.filter(
        Exists(waiting), status=LawyerProfile.Status.LAWYER, notifications=True, notified=False,
    ).select_related('user')
    subject, body = _email()
    sent = 0
    for profile in due:
        # Claimed first, so that two runs at once never email a lawyer twice.
        if not LawyerProfile.objects.filter(pk=profile.pk, notified=False).update(notified=True):
            continue
        try:
            send_mail(subject, body, None, [profile.user.email])
        except OSError:
            # Tried again at the next run. Never which lawyer: the count is enough.
            LawyerProfile.objects.filter(pk=profile.pk).update(notified=False)
            logger.warning('A notification email could not be sent; it will be tried again.')
            continue
        sent += 1
    return sent
