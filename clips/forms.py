from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from .models import Precision
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


class ConfirmForm(forms.Form):
    """Upload step 2: everything is optional (SPEC.md §8). The metadata time is not a field."""

    stated_time = forms.DateTimeField(
        label=_('When was it filmed?'), required=False,
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
    )
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
    metadata_precision = forms.ChoiceField(
        label=_('How precisely may we keep the GPS position found in the video?'),
        choices=METADATA_PRECISIONS, initial='11', widget=forms.RadioSelect,
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

    def __init__(self, *args, has_gps, **kwargs):
        super().__init__(*args, **kwargs)
        if not has_gps:
            del self.fields['metadata_precision']

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
        cleaned = super().clean()
        if 'latitude' in self.errors or 'longitude' in self.errors:
            return cleaned
        latitude, longitude = cleaned.get('latitude'), cleaned.get('longitude')
        if (latitude is None) != (longitude is None):
            raise forms.ValidationError(_('Give both the latitude and the longitude, or neither.'))
        if latitude is not None and not cleaned.get('precision'):
            self.add_error('precision', _('Say how precise this place is.'))
        return cleaned
