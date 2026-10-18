"""The admin's decisions on cases over the size caps (SPEC.md §7).

The admin sees each waiting case's time window and area, margin included, and the
lawyer's reason; not its case label. Deciding deletes the reason, closes a refused case,
and writes an audit entry; the admin may add a note of their own, which is kept.
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from accounts.roles import admin_required
from clips import grid

from . import size
from .forms import DecisionForm
from .models import CaseApproval


@admin_required
def case_requests(request):
    waiting = CaseApproval.objects.waiting().select_related('case__lawyer_profile__user').order_by('requested_at')
    return render(request, 'cases/case_requests.html', {'waiting': waiting})


@admin_required
def case_request(request, case_id):
    approval = get_object_or_404(
        CaseApproval.objects.waiting().select_related('case__lawyer_profile__user'), pk=case_id,
    )
    form = DecisionForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            approval.decide(form.cleaned_data['approved'], request.user, form.cleaned_data['note'])
        except ValueError:
            # Decided by another request, or closed by its lawyer, in the meantime.
            raise Http404 from None
        messages.success(request, _('The case is approved.') if approval.approved else _('The case is refused and closed.'))
        return redirect('manage:case_requests')
    case = approval.case
    area = case.area()
    return render(request, 'cases/case_request.html', {
        'approval': approval, 'case': case, 'form': form, 'cells': len(area),
        'window_minutes': round(size.window_minutes(case.starts_at, case.ends_at, case.margin_minutes)),
        'area_metres': round(size.area_metres(area, case.margin_rings)),
        'centre': grid.centre(grid.middle_cell(area)),
    }, status=400 if form.errors else 200)
