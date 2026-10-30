"""What a lawyer reports about a clip from its page (SPEC.md §7, cases.reports): a doubt, or
inappropriate content, and cancelling their own report. Each goes through one of the lawyer's own
usable cases that the clip matches, as the page itself does.
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.roles import lawyer_required
from clips.models import Report

from . import reports
from .clip_pages import case_and_clip
from .tag_pages import back_to_clip


def _kind(kind):
    if kind not in Report.Kind.values:
        raise Http404
    return Report.Kind(kind)


@lawyer_required
@require_POST
def report(request, case_id, clip_id, kind):
    kind = _kind(kind)
    case, clip, _match = case_and_clip(request, case_id, clip_id)
    try:
        withdrawn = reports.report(case, clip, kind, confirmed=request.POST.get('confirm_withdrawal') == 'yes')
    except reports.Unconfirmed:
        messages.error(request, _('Another lawyer has reported this clip as inappropriate: your report would withdraw it. Tick the box to confirm.'))
    except reports.Refused:
        messages.error(request, _('You have already made this report.'))
    except reports.Gone:
        raise Http404 from None
    else:
        if withdrawn:
            # Its page is gone with it.
            messages.success(request, _('Your report withdrew the clip: no lawyer can find or watch it any more.'))
            return redirect('lawyer:case', case_id=case.pk)
    return back_to_clip(case, clip)


@lawyer_required
@require_POST
def cancel(request, case_id, clip_id, kind):
    kind = _kind(kind)
    case, clip, _match = case_and_clip(request, case_id, clip_id)
    try:
        reports.cancel(case, clip, kind)
    except reports.Refused:
        messages.error(request, _('You have no report of this kind on this clip.'))
    except reports.Gone:
        raise Http404 from None
    return back_to_clip(case, clip)
