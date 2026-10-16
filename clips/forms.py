from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _


class FileForm(forms.Form):
    """Upload step 1: the video file alone (SPEC.md §8)."""

    file = forms.FileField(
        label=_('Video'),
        # Phones open their video gallery for this; the server checks the file anyway.
        widget=forms.FileInput(attrs={'accept': 'video/*'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Read by clips/upload.js to warn about an oversize file before it is sent.
        self.fields['file'].widget.attrs.update({
            'data-max-bytes': settings.UPLOAD_MAX_BYTES,
            'data-too-large-message': 'file-too-large',
        })
