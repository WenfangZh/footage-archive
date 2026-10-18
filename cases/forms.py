import datetime
import zoneinfo

from django import forms
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import LawyerProfile
from clips import grid

from . import size
from .models import ADMIN_NOTE_MAX_LENGTH, REASON_MAX_LENGTH

LABEL_MAX_LENGTH = 100


def margin_fields():
    # Bounds that keep the size check cheap; the size caps refuse far less than these anyway.
    minutes = forms.IntegerField(
        label=_('Margin around the time window, in minutes'), min_value=0,
        max_value=settings.CASE_WINDOW_CEILING_MINUTES,
    )
    rings = forms.IntegerField(
        label=_('Margin around the area, in rings of cells about 50 m wide'), min_value=0,
        max_value=settings.CASE_AREA_CEILING_METRES // 100,
    )
    return minutes, rings


# Footage in the archive is recent; anything outside these years is a typing mistake.
EARLIEST_YEAR = 2000
UTC_ZONE = zoneinfo.ZoneInfo('UTC')


class ParisDateTimeField(forms.DateTimeField):
    """A time typed as on a police report: French time, read without refusing the hour that
    clocks repeat or skip when they change.

    In the repeated autumn hour, the first one is meant; a time in the skipped spring hour
    is read with the winter offset, an hour later in summer time.
    """

    widget = forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M')

    def to_python(self, value):
        # Django's own refuses those hours: read the time as UTC, then put French time on it.
        # A time typed with its own offset comes back with another time zone, and is refused
        # rather than misread.
        with timezone.override(UTC_ZONE):
            parsed = super().to_python(value)
        if parsed is not None and parsed.tzinfo is not UTC_ZONE:
            raise forms.ValidationError(_('Enter the time without a time zone: it is read as French time.'))
        if parsed is None:
            return None
        local = parsed.replace(tzinfo=zoneinfo.ZoneInfo(settings.TIME_ZONE), fold=0)
        if not EARLIEST_YEAR <= local.year <= timezone.now().year + 1:
            raise forms.ValidationError(_('Enter a date between %(first)s and next year.'), params={'first': EARLIEST_YEAR})
        return local


class ForgottenNumberInput(forms.NumberInput):
    """A number field whose value is never sent back to the browser: typed coordinates are not kept."""

    def format_value(self, value):
        return None


class CaseForm(forms.Form):
    """A new case (SPEC.md §7): its case label, time window, area and margin.

    The area is typed as a place and a radius, and kept only as grid cells.
    """

    label = forms.CharField(
        label=_('Case label'), max_length=LABEL_MAX_LENGTH,
        help_text=_("Something to recognise the case by. Never a client's name."),
    )
    starts_at = ParisDateTimeField(
        label=_('Start of the time window'), help_text=_('French time, as in the police report.'),
    )
    ends_at = ParisDateTimeField(label=_('End of the time window'))
    latitude = forms.FloatField(
        label=_('Latitude'), min_value=-90, max_value=90, widget=ForgottenNumberInput(attrs={'step': 'any'}),
    )
    longitude = forms.FloatField(
        label=_('Longitude'), min_value=-180, max_value=180, widget=ForgottenNumberInput(attrs={'step': 'any'}),
        help_text=_('The place in the police report. Only the grid cells around it are kept, not the place itself.'),
    )
    radius_metres = forms.IntegerField(
        label=_('Radius around that place, in metres'), min_value=0,
        max_value=settings.CASE_AREA_CEILING_METRES // 2,
    )
    margin_minutes, margin_rings = margin_fields()
    reason = forms.CharField(
        label=_('Why this case needs to be larger than the size caps'), required=False,
        max_length=REASON_MAX_LENGTH, widget=forms.Textarea,
        help_text=_(
            "Only for a case over the size caps: the admin reads it to decide, then it is deleted. "
            "Never a client's name."
        ),
    )

    def clean(self):
        cleaned = super().clean()
        starts_at, ends_at = cleaned.get('starts_at'), cleaned.get('ends_at')
        if starts_at and ends_at and not starts_at < ends_at:
            self.add_error('ends_at', _('The time window must end after it starts.'))
        place = [cleaned.pop(name, None) for name in ('latitude', 'longitude')]
        if None not in place and cleaned.get('radius_metres') is not None:
            cleaned['cells'] = grid.cells_around(*place, cleaned.pop('radius_metres'))
        if self.errors:
            return cleaned
        cleaned['needs_approval'] = False
        try:
            size.check(starts_at, ends_at, cleaned['cells'], cleaned['margin_minutes'], cleaned['margin_rings'])
        except size.CaseTooLarge as too_large:
            if too_large.beyond_ceiling or not cleaned['reason'].strip():
                self.add_error(None, too_large_message(too_large))
            else:
                cleaned['needs_approval'] = True
        return cleaned


def too_large_message(too_large):
    measured = {'minutes': round(too_large.window_minutes), 'metres': round(too_large.area_metres)}
    if too_large.beyond_ceiling:
        return _(
            'With its margin, this case lasts %(minutes)s minutes and is about %(metres)s m across. '
            'No case can last more than %(ceiling_minutes)s minutes or be more than %(ceiling_metres)s m across.'
        ) % {
            **measured, 'ceiling_minutes': settings.CASE_WINDOW_CEILING_MINUTES,
            'ceiling_metres': settings.CASE_AREA_CEILING_METRES,
        }
    return _(
        'With its margin, this case lasts %(minutes)s minutes and is about %(metres)s m across. Without the '
        "admin's approval, a case lasts at most %(cap_minutes)s minutes and is at most %(cap_metres)s m across. "
        'To ask for it, say why in the last field.'
    ) % {**measured, 'cap_minutes': settings.CASE_WINDOW_CAP_MINUTES, 'cap_metres': settings.CASE_AREA_CAP_METRES}


class DefaultMarginForm(forms.ModelForm):
    """The margin a lawyer's new cases start with."""

    default_margin_minutes, default_margin_rings = margin_fields()

    class Meta:
        model = LawyerProfile
        fields = ['default_margin_minutes', 'default_margin_rings']


class DecisionForm(forms.Form):
    """The admin's decision on a larger case, with an optional note of their own."""

    approved = forms.TypedChoiceField(
        label=_('Decision'), widget=forms.RadioSelect, coerce=lambda value: value == 'yes',
        choices=[('yes', _('Approve the case')), ('no', _('Refuse the case: it is closed'))],
    )
    note = forms.CharField(
        label=_('Your note to the lawyer (optional)'), required=False, max_length=ADMIN_NOTE_MAX_LENGTH,
        widget=forms.Textarea, help_text=_("Kept with the case, which its lawyer can see. Never a client's name."),
    )
