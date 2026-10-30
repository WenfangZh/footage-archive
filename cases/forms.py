import datetime
import math
import re
import unicodedata
import zoneinfo

from django import forms
from django.conf import settings
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_variables
from django.utils.translation import gettext_lazy as _

from accounts.models import LawyerProfile
from clips import grid
from clips.forms import MESSAGE_MAX_LENGTH
from clips.models import Message
from clips.tags import CUSTOM_TAG_MAX_LENGTH, DEFAULT_TAGS

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


# An H3 id in hexadecimal, as the map's h3-js writes it; grid.is_cell checks the rest.
HEX_ID = re.compile(r'[0-9a-f]{1,16}', re.IGNORECASE)


def max_cells():
    """More cells than any area within the ceiling holds: such a set is refused before it is measured.

    An area at most D across fits in a circle D/√3 in radius (Jung's theorem); twice that
    circle's area in cells of average size leaves room for cells smaller than average.
    """
    radius = settings.CASE_AREA_CEILING_METRES / math.sqrt(3)
    return max(1, int(2 * math.pi * radius**2 / grid.average_area_square_metres()))


class CellsField(forms.Field):
    """An area picked on the map (M4.5): the grid's finest cells, as H3 ids in hexadecimal.

    Only these ids reach the server, never a click or the map's view (ADR 0006), and they
    are checked like anything else a browser sends.
    """

    widget = forms.HiddenInput
    default_error_messages = {
        'invalid': _('The area picked on the map could not be read. Pick it again.'),
        'too_many': _('The area picked on the map has more cells than any case can hold.'),
    }

    def to_python(self, value):
        ids = (value or '').replace(',', ' ').split()
        if not ids:
            return None
        if len(ids) > max_cells():
            raise forms.ValidationError(self.error_messages['too_many'], code='too_many')
        cells = set()
        for text in ids:
            cell = int(text, 16) if HEX_ID.fullmatch(text) else None
            if cell in cells or not grid.is_cell(cell) or grid.resolution(cell) != grid.finest_resolution():
                raise forms.ValidationError(self.error_messages['invalid'], code='invalid')
            cells.add(cell)
        return cells


PLACE_FIELDS = ('latitude', 'longitude', 'radius_metres')


class CaseForm(forms.Form):
    """A new case (SPEC.md §7): its case label, time window, area and margin.

    The area is typed as a place and a radius, or picked on the map as cells; either way it
    is kept only as grid cells.
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
        label=_('Latitude'), min_value=-90, max_value=90, required=False,
        widget=ForgottenNumberInput(attrs={'step': 'any'}),
    )
    longitude = forms.FloatField(
        label=_('Longitude'), min_value=-180, max_value=180, required=False,
        widget=ForgottenNumberInput(attrs={'step': 'any'}),
        help_text=_('The place in the police report. Only the grid cells around it are kept, not the place itself.'),
    )
    radius_metres = forms.IntegerField(
        label=_('Radius around that place, in metres'), min_value=0, required=False,
        max_value=settings.CASE_AREA_CEILING_METRES // 2,
    )
    cells = CellsField(required=False)
    margin_minutes, margin_rings = margin_fields()
    reason = forms.CharField(
        label=_('Why this case needs to be larger than the size caps'), required=False,
        max_length=REASON_MAX_LENGTH, widget=forms.Textarea,
        help_text=_(
            "Only for a case over the size caps: the admin reads your reason to decide, then it is deleted; "
            "for a case within them, it is not kept. Never a client's name."
        ),
    )

    @method_decorator(sensitive_variables('cleaned', 'place'))
    def clean(self):
        cleaned = super().clean()
        starts_at, ends_at = cleaned.get('starts_at'), cleaned.get('ends_at')
        if starts_at and ends_at and not starts_at < ends_at:
            self.add_error('ends_at', _('The time window must end after it starts.'))
        self._clean_area(cleaned)
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

    @method_decorator(sensitive_variables('cleaned', 'place', 'value'))
    def _clean_area(self, cleaned):
        """Put the area's cells in `cleaned`, from the map or from the typed place and radius."""
        picked = cleaned.pop('cells', None)
        place = [cleaned.pop(name, None) for name in PLACE_FIELDS]
        typed = any(self.data.get(name, '').strip() for name in PLACE_FIELDS)
        if picked and typed:
            self.add_error(None, _('Give the area either as a place and a radius or on the map, not both.'))
        elif picked:
            cleaned['cells'] = picked
        elif typed and 'cells' not in self.errors:
            for name, value in zip(PLACE_FIELDS, place):
                if value is None and name not in self.errors:
                    self.add_error(name, self.fields[name].error_messages['required'])
            if None not in place:
                cleaned['cells'] = grid.cells_around(*place)
        elif not typed and 'cells' not in self.errors:
            self.add_error(None, _('Give the place and radius from the police report, or pick the area on the map.'))


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
        'To ask for it, give your reason in the field "Why this case needs to be larger than the size caps".'
    ) % {**measured, 'cap_minutes': settings.CASE_WINDOW_CAP_MINUTES, 'cap_metres': settings.CASE_AREA_CAP_METRES}


class SettingsForm(forms.ModelForm):
    """A lawyer's settings (SPEC.md §7): the margin their new cases start with, whether the code
    holder sees their name on their messages by default, and email notifications."""

    default_margin_minutes, default_margin_rings = margin_fields()

    class Meta:
        model = LawyerProfile
        fields = ['default_margin_minutes', 'default_margin_rings', 'identified_by_default', 'notifications']
        labels = {
            'identified_by_default': _('Show code holders my name and bar on my messages'),
            'notifications': _('Email me when something is waiting for me in the archive'),
        }
        help_texts = {
            'identified_by_default': _('Otherwise they see only "a verified defence lawyer". You can still choose on each message.'),
            'notifications': _('At most one email until you open your messages, saying only that something is waiting: never what, nor about which clip.'),
        }


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


class LawyerTagForm(forms.Form):
    """One tag: a default tag, or a custom one."""

    key = forms.ChoiceField(
        label=_('Default tag'), required=False, choices=[('', '—'), *DEFAULT_TAGS.items()],
    )
    custom_name = forms.CharField(
        label=_('Or your own tag'), required=False, max_length=CUSTOM_TAG_MAX_LENGTH,
        help_text=_(
            'Every lawyer whose case matches this clip will see it. Write only what can be seen, nothing about your case.'
        ),
    )

    def clean_custom_name(self):
        # Composed, so that the same text typed or pasted another way is the same tag.
        name = unicodedata.normalize('NFC', self.cleaned_data['custom_name']).strip()
        if name and not any(character.isalnum() for character in name):
            raise forms.ValidationError(_('A tag needs at least one letter or digit.'))
        return name

    def clean(self):
        cleaned = super().clean()
        if 'custom_name' in self.errors:
            return cleaned
        if bool(cleaned.get('key')) == bool(cleaned.get('custom_name')):
            raise forms.ValidationError(_('Choose a default tag or write your own, not both.'))
        return cleaned


class MessageForm(forms.Form):
    """A message to a clip's code holder, or a reply in a thread."""

    text = forms.CharField(
        label=_('Message'), max_length=MESSAGE_MAX_LENGTH, widget=forms.Textarea,
        help_text=_('Stored so that only the code holder of this clip, with their upload code, and you, with your personal key, can read it.'),
    )
    identified = forms.BooleanField(
        label=_('Show the code holder my name and bar on this message'), required=False,
        help_text=_('Otherwise they see only "a verified defence lawyer".'),
    )


class WrongReportForm(forms.Form):
    """A report that a clip's time or place is wrong: a message to its code holder, whose note is
    optional (SPEC.md §7)."""

    kind = forms.ChoiceField(
        label=_('What is wrong'), widget=forms.RadioSelect,
        choices=[(Message.Kind.WRONG_TIME, _('The time')), (Message.Kind.WRONG_PLACE, _('The place'))],
    )
    note = forms.CharField(
        label=_('Note (optional)'), max_length=MESSAGE_MAX_LENGTH, widget=forms.Textarea, required=False,
        help_text=_('Stored so that only the code holder of this clip, with their upload code, and you, with your personal key, can read it.'),
    )
    # The same choice as on a message.
    identified = MessageForm.base_fields['identified']
