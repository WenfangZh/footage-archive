"""Lawyers' reports that count (SPEC.md §7): a doubt about a clip's authenticity, and reports of
inappropriate content, the second of which, from a different lawyer, withdraws the clip for good.
A report of a wrong time or place is a message to the code holder instead.

Each function takes the lawyer's usable case that matches the clip, which the view checks
(cases.clip_pages.case_and_clip), and writes an audit entry. Only reports from admitted,
unsuspended lawyers count, as tag proposals do (cases.lawyer_tags): a suspended or revoked
lawyer's report shows nothing and withdraws nothing, but a withdrawal stays.
"""

from django.db import transaction

from accounts.models import LawyerProfile
from audit.models import Action, record
from clips.models import Clip, Report

# Reports of inappropriate content from this many different lawyers withdraw a clip.
REPORTS_TO_WITHDRAW = 2

REPORT_ACTIONS = {Report.Kind.DOUBT: Action.REPORT_DOUBT, Report.Kind.INAPPROPRIATE: Action.REPORT_INAPPROPRIATE}
CANCELLING_ACTIONS = {Report.Kind.DOUBT: Action.DOUBT_CANCELLING, Report.Kind.INAPPROPRIATE: Action.INAPPROPRIATE_CANCELLING}


class Refused(Exception):
    """The lawyer already made this report, or has none to cancel."""


class Unconfirmed(Exception):
    """The report would withdraw the clip, which the lawyer did not confirm."""


class Gone(Exception):
    """The clip was deleted or withdrawn, or is no longer ready, since the request found it."""


def counting(clip):
    """The clip's reports that count."""
    return Report.objects.filter(clip=clip, lawyer_profile__status=LawyerProfile.Status.LAWYER)


def _locked_clip(clip):
    # Locked, so that two lawyers reporting at once both count, and withdraw the clip once.
    if not Clip.objects.select_for_update().filter(pk=clip.pk, status=Clip.Status.READY).exists():
        raise Gone


def report(case, clip, kind, confirmed=False):
    """Report the clip; True if this withdrew it, which a report of inappropriate content does
    only `confirmed`: what the lawyer saw may predate the other lawyer's report."""
    profile = case.lawyer_profile
    with transaction.atomic():
        _locked_clip(clip)
        if Report.objects.filter(clip=clip, lawyer_profile=profile, kind=kind).exists():
            raise Refused
        withdraws = kind == Report.Kind.INAPPROPRIATE and (
            counting(clip).filter(kind=kind).count() + 1 >= REPORTS_TO_WITHDRAW
        )
        if withdraws and not confirmed:
            raise Unconfirmed
        Report.objects.create(clip=clip, lawyer_profile=profile, kind=kind)
        record(REPORT_ACTIONS[kind], profile, case=case, clip=clip)
        if withdraws:
            # The reports behind it stay, and can no longer be cancelled.
            Clip.objects.filter(pk=clip.pk).update(status=Clip.Status.WITHDRAWN)
            record(Action.CLIP_WITHDRAWAL, profile, case=case, clip=clip)
    return withdraws


def cancel(case, clip, kind):
    """Cancel the lawyer's own report, while the clip is not withdrawn."""
    profile = case.lawyer_profile
    with transaction.atomic():
        _locked_clip(clip)
        deleted, _ = Report.objects.filter(clip=clip, lawyer_profile=profile, kind=kind).delete()
        if not deleted:
            raise Refused
        record(CANCELLING_ACTIONS[kind], profile, case=case, clip=clip)


def shown(clip, profile):
    """What the clip's page shows the lawyer: the doubt marker and the content warning, from other
    lawyers' reports, and the kinds of report the lawyer made themselves, which they can cancel."""
    others = set(counting(clip).exclude(lawyer_profile=profile).values_list('kind', flat=True))
    own = set(Report.objects.filter(clip=clip, lawyer_profile=profile).values_list('kind', flat=True))
    return {
        'doubt_marker': Report.Kind.DOUBT in others,
        'content_warning': Report.Kind.INAPPROPRIATE in others,
        'own_doubt': Report.Kind.DOUBT in own,
        'own_inappropriate_report': Report.Kind.INAPPROPRIATE in own,
    }
