"""What a browser sends when it has made a personal key: checked once, here (design §5.1).

Only the public half and the two encrypted copies of the private half arrive, with the
salt for Argon2id. The key passphrase and the recovery key never leave the browser.
"""

import base64
import binascii

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import ENCRYPTED_PRIVATE_KEY_BYTES, PUBLIC_KEY_BYTES, SALT_BYTES


class Base64BytesField(forms.CharField):
    """Exactly `length` bytes, sent in standard base64."""

    widget = forms.HiddenInput

    def __init__(self, length, **kwargs):
        self.length = length
        super().__init__(max_length=(length + 2) // 3 * 4, **kwargs)

    def to_python(self, value):
        value = super().to_python(value)
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            raise forms.ValidationError(_('The key could not be read.')) from None
        if len(decoded) != self.length:
            raise forms.ValidationError(_('The key could not be read.'))
        return decoded


class KeySetupForm(forms.Form):
    public_key = Base64BytesField(PUBLIC_KEY_BYTES)
    passphrase_salt = Base64BytesField(SALT_BYTES)
    private_key_under_passphrase = Base64BytesField(ENCRYPTED_PRIVATE_KEY_BYTES)
    private_key_under_recovery_key = Base64BytesField(ENCRYPTED_PRIVATE_KEY_BYTES)
