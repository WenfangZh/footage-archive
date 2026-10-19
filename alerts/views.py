import datetime

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.validators import MaxValueValidator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from accounts.models import LawyerProfile
from accounts.roles import admin_required
from audit.models import Action, record

from .checks import ACTIVITIES, activity_limit
from .models import Alert, AlertLimitRaise


@admin_required
@require_POST
def mark_seen(request, alert_id):
    alert = get_object_or_404(Alert, pk=alert_id, seen_at__isnull=True)
    alert.seen_at = timezone.now()
    alert.save(update_fields=['seen_at'])
    return redirect('manage:home')


class RaiseForm(forms.Form):
    activity = forms.ChoiceField(label=_('Alert'), choices=AlertLimitRaise.Activity.choices, widget=forms.RadioSelect)
    limit = forms.IntegerField(label=_('New limit'), min_value=1)
    days = forms.IntegerField(label=_('For how many days'), min_value=1)

    def __init__(self, *args, profile, **kwargs):
        super().__init__(*args, **kwargs)
        self.profile = profile
        self.fields['days'].validators.append(MaxValueValidator(settings.ALERT_RAISE_MAX_DAYS))
        self.fields['days'].widget.attrs['max'] = settings.ALERT_RAISE_MAX_DAYS
        self.fields['days'].help_text = _('At most %(days)s.') % {'days': settings.ALERT_RAISE_MAX_DAYS}

    def clean(self):
        cleaned = super().clean()
        activity, limit = cleaned.get('activity'), cleaned.get('limit')
        if activity and limit is not None:
            cleaned['before'] = activity_limit(self.profile, activity)
            default = getattr(settings, ACTIVITIES[activity][1])
            if limit <= cleaned['before']:
                self.add_error('limit', _('A raise must be higher than the limit now: %(limit)s.') % {'limit': cleaned['before']})
            elif limit > 10 * default:
                self.add_error('limit', _('At most ten times the usual limit: %(limit)s.') % {'limit': 10 * default})
        return cleaned


@admin_required
def raise_limit(request, profile_id):
    """Raise one lawyer's limit for an activity alert for a few days; the raise is logged (SPEC.md §7)."""
    profile = get_object_or_404(
        LawyerProfile.objects.select_related('user'), pk=profile_id, status=LawyerProfile.Status.LAWYER,
    )
    form = RaiseForm(request.POST if request.method == 'POST' else None, profile=profile)
    if request.method == 'POST' and form.is_valid():
        activity, limit = form.cleaned_data['activity'], form.cleaned_data['limit']
        setting = ACTIVITIES[activity][1]
        now = timezone.now()
        with transaction.atomic():
            AlertLimitRaise.objects.create(
                lawyer_profile=profile, activity=activity, limit=limit, raised_at=now,
                until=now + datetime.timedelta(days=form.cleaned_data['days']),
            )
            record(
                Action.ALERT_LIMIT_RAISE, profile, admin=request.user,
                limits={
                    'before': {setting: form.cleaned_data['before']}, 'after': {setting: limit},
                    'days': form.cleaned_data['days'],
                },
            )
        messages.success(request, _('The alert limit is raised.'))
        return redirect('manage:home')
    raises = AlertLimitRaise.objects.filter(lawyer_profile=profile, until__gt=timezone.now()).order_by('until')
    return render(request, 'alerts/raise_limit.html', {
        'profile': profile, 'form': form, 'raises': raises,
        'usual': {'downloads': settings.ALERT_DOWNLOADS, 'cases': settings.ALERT_CASES},
    }, status=400 if form.errors else 200)
