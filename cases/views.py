"""A lawyer's own case pages (SPEC.md §7): creating a case, seeing one, and setting the default margin."""

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables

from accounts.models import LawyerProfile
from accounts.roles import lawyer_required
from audit.models import Action, record

from . import size
from .forms import CaseForm, DefaultMarginForm, too_large_message
from .models import Case, CaseApproval


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
