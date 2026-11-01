import re

from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from . import grid
from .models import Precision, stated_cells_for_point
from .tags import CUSTOM_TAG_MAX_LENGTH, DEFAULT_TAGS

CONTEXT_MAX_LENGTH = 4000
CONTACT_DETAILS_MAX_LENGTH = 500
MAX_CUSTOM_TAGS = 10

# Cell sizes offered for the metadata location (ADR 0004), finest first.
METADATA_PRECISIONS = [
    ('11', _('About 50 m')),
    ('10', _('About 130 m')),
    ('9', _('About 340 m')),
]


# An H3 id in hexadecimal, as the map's h3-js writes it; grid.is_cell checks the rest.
HEX_ID = re.compile(r'[0-9a-f]{1,16}', re.IGNORECASE)


def max_stated_cells():
    return grid.max_cells_within(settings.STATED_LOCATION_CEILING_METRES)


class CellsField(forms.Field):
    """Cells picked on a map: the grid's finest cells, as H3 ids in hexadecimal (a case's area,
    M4.5; a clip's stated location, M6.6). Only these ids reach the server, never a click or the
    map's view (ADR 0006, ADR 0009), and they are checked like anything else a browser sends.
    `max_cells` says how many are too many to measure.
    """

    widget = forms.HiddenInput
    default_error_messages = {
        'invalid': _('The place painted on the map could not be read. Paint it again.'),
        'too_many': _('The place painted on the map is too large.'),
    }

    def __init__(self, *, max_cells, **kwargs):
        self.max_cells = max_cells
        super().__init__(**kwargs)

    def prepare_value(self, value):
        if isinstance(value, (list, set, tuple)):
            return ' '.join(format(cell, 'x') for cell in sorted(value))
        return value

    def to_python(self, value):
        ids = (value or '').replace(',', ' ').split()
        if not ids:
            return None
        if len(ids) > self.max_cells():
            raise forms.ValidationError(self.error_messages['too_many'], code='too_many')
        cells = set()
        for text in ids:
            cell = int(text, 16) if HEX_ID.fullmatch(text) else None
            if cell in cells or not grid.is_cell(cell) or grid.resolution(cell) != grid.finest_resolution():
                raise forms.ValidationError(self.error_messages['invalid'], code='invalid')
            cells.add(cell)
        return cells


class FileForm(forms.Form):
    """Upload step 1: the video file alone (SPEC.md §8)."""

    file = forms.FileField(
        label=_('Video'),
        # Phones open their video gallery for this; the server checks the file anyway.
        widget=forms.FileInput(attrs={'accept': 'video/*'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Read by clips/forms.js to warn about an oversize file before it is sent.
        self.fields['file'].widget.attrs.update({
            'data-max-bytes': settings.UPLOAD_MAX_BYTES,
            'data-too-large-message': 'file-too-large',
        })


class ClipFieldsForm(forms.Form):
    """What the uploader gives at upload and the code holder can change later (SPEC.md §5, §8).

    Everything is optional. The metadata time is never a field.
    """

    stated_time = forms.DateTimeField(
        label=_('When was it filmed?'), required=False,
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
    )
    # The place painted on the map (ADR 0009). Coordinates typed below, when given, replace it.
    # Looked up at each use, so that a test can lower it.
    cells = CellsField(required=False, max_cells=lambda: max_stated_cells())
    latitude = forms.FloatField(
        label=_('Latitude'), required=False, min_value=-90, max_value=90,
        widget=forms.NumberInput(attrs={'step': 'any'}),
    )
    longitude = forms.FloatField(
        label=_('Longitude'), required=False, min_value=-180, max_value=180,
        widget=forms.NumberInput(attrs={'step': 'any'}),
    )
    precision = forms.ChoiceField(
        label=_('How precise is this place?'), required=False,
        choices=Precision.choices, widget=forms.RadioSelect,
    )
    tags = forms.MultipleChoiceField(
        label=_('What can be seen?'), required=False,
        choices=list(DEFAULT_TAGS.items()), widget=forms.CheckboxSelectMultiple,
    )
    custom_tags = forms.CharField(
        label=_('Other tags, separated by commas'), required=False,
        max_length=MAX_CUSTOM_TAGS * (CUSTOM_TAG_MAX_LENGTH + 2),
    )
    context = forms.CharField(
        label=_('Context'), required=False, max_length=CONTEXT_MAX_LENGTH, widget=forms.Textarea,
        help_text=_(
            'What happened, and anything a lawyer should know. Leave out names, and '
            'addresses more precise than the place above.'
        ),
    )
    contact_details = forms.CharField(
        label=_('Contact details or social media handle'), required=False,
        max_length=CONTACT_DETAILS_MAX_LENGTH,
    )

    def clean_custom_tags(self):
        names = []
        for name in self.cleaned_data['custom_tags'].split(','):
            name = name.strip()
            if not name or name in names:
                continue
            if len(name) > CUSTOM_TAG_MAX_LENGTH:
                raise forms.ValidationError(
                    _('Each tag can be at most %(length)d characters.'),
                    params={'length': CUSTOM_TAG_MAX_LENGTH},
                )
            names.append(name)
        if len(names) > MAX_CUSTOM_TAGS:
            raise forms.ValidationError(
                _('At most %(count)d other tags.'), params={'count': MAX_CUSTOM_TAGS},
            )
        return names

    def clean(self):
        """The stated location, as cleaned['stated_cells']: the typed point's cells, or else the
        cells painted; None for none."""
        cleaned = super().clean()
        if 'latitude' in self.errors or 'longitude' in self.errors or 'cells' in self.errors:
            return cleaned
        latitude, longitude = cleaned.get('latitude'), cleaned.get('longitude')
        if (latitude is None) != (longitude is None):
            raise forms.ValidationError(_('Give both the latitude and the longitude, or neither.'))
        if latitude is not None and not cleaned.get('precision'):
            self.add_error('precision', _('Say how precise this place is.'))
            return cleaned
        if latitude is not None:
            cleaned['stated_cells'] = stated_cells_for_point(latitude, longitude, cleaned['precision'])
        else:
            cleaned['stated_cells'] = cleaned.get('cells')
        if cleaned['stated_cells'] and grid.span_metres(cleaned['stated_cells']) > settings.STATED_LOCATION_CEILING_METRES:
            self.add_error('cells', forms.ValidationError(
                _('The place painted on the map is more than %(metres)d m across.'),
                params={'metres': settings.STATED_LOCATION_CEILING_METRES},
            ))
        return cleaned


class ConfirmForm(ClipFieldsForm):
    """Upload step 2 (SPEC.md §8)."""

    metadata_precision = forms.ChoiceField(
        label=_('How precisely may we keep the GPS position found in the video?'),
        choices=METADATA_PRECISIONS, initial='11', widget=forms.RadioSelect,
    )

    def __init__(self, *args, has_gps, **kwargs):
        super().__init__(*args, **kwargs)
        if not has_gps:
            del self.fields['metadata_precision']


class EditForm(ClipFieldsForm):
    """The code holder's changes to a clip (SPEC.md §5). The code comes back with each change."""

    code = forms.CharField(widget=forms.HiddenInput)
    # Without JavaScript the page sends back the cells kept, and empty coordinates keep them: this
    # is how a place is removed then (SPEC.md §8).
    remove_place = forms.BooleanField(
        label=_('Remove the place'), required=False,
        help_text=_('No lawyer can find the clip until you give a place again.'),
    )
    metadata_precision = forms.ChoiceField(
        label=_('How precisely we keep the GPS position found in the video'),
        widget=forms.RadioSelect,
    )

    def __init__(self, *args, metadata_resolution, **kwargs):
        super().__init__(*args, **kwargs)
        # The metadata location can only be made coarser (ADR 0004).
        coarser = [(value, label) for value, label in METADATA_PRECISIONS
                   if metadata_resolution is not None and int(value) <= metadata_resolution]
        if len(coarser) > 1:
            self.fields['metadata_precision'].choices = coarser
            self.fields['metadata_precision'].initial = str(metadata_resolution)
        else:
            del self.fields['metadata_precision']

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('remove_place'):
            cleaned['stated_cells'] = None  # Whatever else was sent for the place.
        return cleaned


# A message's or reply's length, either way: lawyers' (cases.forms) and the code holder's.
MESSAGE_MAX_LENGTH = 4000


class ReplyForm(forms.Form):
    """The code holder's reply in one lawyer's thread. The code comes back with it."""

    code = forms.CharField(widget=forms.HiddenInput)
    # The thread's first message, which names it without naming its lawyer.
    thread = forms.UUIDField(widget=forms.HiddenInput)
    text = forms.CharField(label=_('Your reply'), max_length=MESSAGE_MAX_LENGTH, widget=forms.Textarea)
