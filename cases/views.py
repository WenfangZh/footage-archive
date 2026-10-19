"""A lawyer's own case pages (SPEC.md §7): creating a case, seeing one, and setting the default margin."""

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_POST

from accounts.models import LawyerProfile
from accounts.roles import lawyer_required
from alerts.checks import check_activity
from alerts.models import AlertLimitRaise
from audit.models import Action, record

from . import size
from .forms import CaseForm, DefaultMarginForm, too_large_message
from .models import Case, CaseApproval
from .search import find


def own_profile(request):
    return LawyerProfile.objects.get(user=request.user)


@lawyer_required
# Left out of Django's error reports: the typed place is never kept.
# The reason is deleted after the admin's decision, so it stays out of them too.
@sensitive_post_parameters('latitude', 'longitude', 'reason')
# And out of the variables a traceback shows: the form and its cleaned data hold them too.
@sensitive_variables('form', 'fields')
def new_case(request):
    profile = own_profile(request)
    if request.method == 'POST':
        form = CaseForm(request.POST)
        if form.is_valid():
            fields = form.cleaned_data
            try:
                with transaction.atomic():
                    case = Case.objects.create_with_area(
                        profile, fields['label'], fields['starts_at'], fields['ends_at'], fields['cells'],
                        fields['margin_minutes'], fields['margin_rings'],
                        approval_reason=fields['reason'] if fields['needs_approval'] else '',
                    )
                    record(Action.CASE_CREATION, profile, case=case)
                    check_activity(profile, AlertLimitRaise.Activity.CASES)
                    if fields['needs_approval']:
                        record(Action.LARGER_CASE_REQUEST, profile, case=case)
            except size.CaseTooLarge as too_large:
                # Only if the caps were lowered since the form was checked.
                form.add_error(None, too_large_message(too_large))
            else:
                return redirect('lawyer:case', case_id=case.pk)
    else:
        form = CaseForm(initial={
            'margin_minutes': profile.default_margin_minutes, 'margin_rings': profile.default_margin_rings,
        })
    return render(request, 'cases/new_case.html', {'form': form}, status=400 if form.errors else 200)


@lawyer_required
def case(request, case_id):
    own_case = get_object_or_404(Case, pk=case_id, lawyer_profile__user=request.user)
    area = own_case.area()
    return render(request, 'cases/case.html', {
        'case': own_case, 'cells': len(area), 'area_metres': round(size.area_metres(area, 0)),
        'approval': CaseApproval.objects.filter(case=own_case).first(),
        # The case's own audit entries: its lawyer may read their own (SPEC.md §7).
        'entries': own_case.audit_entries.order_by('at', 'pk'),
        'usable': Case.objects.usable().filter(pk=own_case.pk).exists(),
    })


@lawyer_required
def default_margin(request):
    form = DefaultMarginForm(request.POST if request.method == 'POST' else None, instance=own_profile(request))
    if request.method == 'POST' and form.is_valid():
        # These two fields only: the rest of the profile may have changed since it was read.
        form.instance.save(update_fields=['default_margin_minutes', 'default_margin_rings'])
        messages.success(request, _('Your new cases will start with this margin.'))
        return redirect('lawyer:home')
    return render(request, 'cases/default_margin.html', {'form': form}, status=400 if form.errors else 200)


@lawyer_required
def close_case(request, case_id):
    """Closing a case, after a confirmation: it can never be reopened."""
    own_case = get_object_or_404(Case.objects.open(), pk=case_id, lawyer_profile__user=request.user)
    if request.method == 'POST':
        Case.objects.filter(pk=own_case.pk).close(Case.ClosedBecause.LAWYER)
        messages.success(request, _('The case is closed.'))
        return redirect('lawyer:case', case_id=own_case.pk)
    return render(request, 'cases/close_case.html', {'case': own_case})


@lawyer_required
def closed_cases(request):
    cases = Case.objects.filter(lawyer_profile__user=request.user, closed_at__isnull=False).order_by('-closed_at')
    return render(request, 'cases/closed_cases.html', {'cases': cases})


@lawyer_required
@require_POST
def search(request, case_id):
    """Run the case's search: every search is logged with how many clips it found (SPEC.md §7).

    Only on POST, from the case page's button: a link, a reload or a prefetch never logs a
    search the lawyer did not ask for. The results are this response, not a page to go back to.
    """
    own_case = get_object_or_404(Case.objects.usable(), pk=case_id, lawyer_profile__user=request.user)
    found = find(own_case)
    record(
        Action.SEARCH, own_case.lawyer_profile, case=own_case,
        core_count=len(found.core), margin_count=len(found.margin),
    )
    return render(request, 'cases/search.html', {'case': own_case, 'core': found.core, 'margin': found.margin})
