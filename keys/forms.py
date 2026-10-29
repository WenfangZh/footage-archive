"""What a browser sends when it has made a key or a grant: checked once, here (design §5.1, §5.3).

For a personal key, only the public half and the two encrypted copies of the private half
arrive, with the salt for Argon2id. The key passphrase and the recovery key never leave the
browser. A group key's private half arrives only sealed to a lawyer's personal key.
"""

import base64
import binascii
import json
import uuid

from django import forms
from django.utils.translation import gettext_lazy as _

from clips.models import SEALED_DATA_KEY_BYTES

from .models import ENCRYPTED_PRIVATE_KEY_BYTES, PUBLIC_KEY_BYTES, SALT_BYTES, SEALED_GROUP_KEY_BYTES, LawyerKey


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

    def clean_public_key(self):
        # One the server can seal case labels to, and a key no one in the archive has had: the
        # safety number the admin compares then names one person's key only.
        public_key = sealable(self.cleaned_data['public_key'])
        if LawyerKey.objects.filter(public_key=public_key).exists():
            raise forms.ValidationError(_('The key could not be read.'))
        return public_key


class NewPassphraseForm(forms.Form):
    """The private key encrypted under a new key passphrase, which the browser set after opening
    the key with the recovery key (design §5.6); the public key names which key it is for."""

    public_key = Base64BytesField(PUBLIC_KEY_BYTES)
    passphrase_salt = Base64BytesField(SALT_BYTES)
    private_key_under_passphrase = Base64BytesField(ENCRYPTED_PRIVATE_KEY_BYTES)


class LoginPasswordForm(forms.Form):
    """Asks for the login password again, before what cannot be undone: a session left open is
    not enough. Checked with keys.views.password_checked, under the login's limits."""

    password = forms.CharField(
        label=_('Your login password:'), strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}),
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self):
        # Django's own check, as at login.
        if not self.user.check_password(self.cleaned_data['password']):
            raise forms.ValidationError(_('That is not your login password.'))
        return ''


class KeyResetForm(LoginPasswordForm):
    # The key the page offered to reset: one set up since, in another tab, is not.
    key = forms.IntegerField(widget=forms.HiddenInput)
    lost_both = forms.BooleanField(label=_('I have lost both my key passphrase and my recovery key.'))
    field_order = ['key', 'lost_both', 'password']


class KeyConfirmationForm(forms.Form):
    # The key whose safety number the page showed: one made since was not the one compared.
    key = forms.IntegerField(widget=forms.HiddenInput)
    key_confirmed = forms.BooleanField(
        label=_("I compared this safety number with them, through the bar directory's contact details."),
    )


# X25519 public keys are numbers below 2**255 - 19, written in 32 bytes, little-endian.
CURVE_PRIME = 2 ** 255 - 19


def sealable(public_key):
    """A public key a data key can be sealed to, in its one encoding. libsodium refuses some
    32-byte values (points of small order), which would make every upload and edit fail once
    they were a group key; and X25519 ignores the top bit, so another encoding of a key would
    pass a comparison of bytes, such as the one refusing the old group key."""
    from nacl.exceptions import CryptoError
    from nacl.public import PublicKey, SealedBox
    if int.from_bytes(public_key, 'little') >= CURVE_PRIME:
        raise forms.ValidationError(_('The key could not be read.'))
    try:
        SealedBox(PublicKey(public_key)).encrypt(b'test')
    except (CryptoError, RuntimeError, ValueError):
        raise forms.ValidationError(_('The key could not be read.')) from None
    return public_key


class GroupKeyForm(forms.Form):
    """A group key made in the first lawyer's browser, and its grant to them (design §5.3)."""

    public_key = Base64BytesField(PUBLIC_KEY_BYTES)
    sealed_private_key = Base64BytesField(SEALED_GROUP_KEY_BYTES)

    def clean_public_key(self):
        return sealable(self.cleaned_data['public_key'])


def json_value(parse, value):
    """Django's JSON parsing, refusing what makes Python's json raise RecursionError."""
    try:
        return parse(value)
    except RecursionError:
        raise forms.ValidationError(_('The key could not be read.')) from None


class SealedGroupKeysField(forms.JSONField):
    """{group key id: the group private key sealed to the recipient}, as a JSON object."""

    widget = forms.HiddenInput

    def to_python(self, value):
        value = json_value(super().to_python, value)
        sealed = Base64BytesField(SEALED_GROUP_KEY_BYTES)
        if not isinstance(value, dict) or not all(key.isascii() and key.isdigit() and len(key) <= 18 for key in value):
            raise forms.ValidationError(_('The key could not be read.'))
        return {int(key): sealed.clean(item) for key, item in value.items()}


class GrantForm(forms.Form):
    grants = SealedGroupKeysField()
    safety_number_compared = forms.BooleanField(
        label=_('I compared this safety number with them, by phone or in person.'),
    )


class SealedByIdField(forms.JSONField):
    """{id: a key sealed to something, `length` bytes in base64}, as a JSON object of at most
    `limit` entries. Ids are whole numbers, or with `uuid_ids` a message's UUIDs."""

    widget = forms.HiddenInput

    def __init__(self, length, limit, uuid_ids=False, **kwargs):
        self.length, self.limit, self.uuid_ids = length, limit, uuid_ids
        super().__init__(**kwargs)

    def to_python(self, value):
        value = json_value(super().to_python, value)
        sealed = Base64BytesField(self.length)
        valid_id = uuid_id if self.uuid_ids else lambda key: key.isascii() and key.isdigit() and len(key) <= 18
        if not isinstance(value, dict) or len(value) > self.limit or not all(valid_id(key) for key in value):
            raise forms.ValidationError(_('The key could not be read.'))
        to_id = uuid.UUID if self.uuid_ids else int
        return {to_id(key): sealed.clean(item) for key, item in value.items()}


def uuid_id(value):
    """Whether `value` is a UUID as Django writes one in JSON: 36 characters, lower case."""
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        return False


class NewGroupKeyForm(forms.Form):
    """A rotator's new group key, sealed to each lawyer key holding the old one (design §5.5)."""

    public_key = Base64BytesField(PUBLIC_KEY_BYTES)
    grants = SealedByIdField(SEALED_GROUP_KEY_BYTES, limit=10_000)

    def clean_public_key(self):
        return sealable(self.cleaned_data['public_key'])


class ResealedForm(forms.Form):
    """Data keys a rotator resealed to the new group key, by the id of the one sealed to the old."""

    resealed = SealedByIdField(SEALED_DATA_KEY_BYTES, limit=500)


def item_ids(value, limit=10_000):
    """A JSON list of ids of sealed keys (clips.models.SealedKey), checked once, here."""
    try:
        items = json.loads(value or '[]')
    except (ValueError, RecursionError):  # Also a number too long, or arrays nested too deep.
        raise forms.ValidationError(_('The key could not be read.')) from None
    if not isinstance(items, list) or len(items) > limit or not all(
        type(item) is int and 0 < item < 10 ** 18 for item in items
    ):
        raise forms.ValidationError(_('The key could not be read.'))
    return items


class ItemIdsForm(forms.Form):
    """Data keys the rotator's browser could not open (design §5.5)."""

    ids = forms.CharField()

    def clean_ids(self):
        return item_ids(self.cleaned_data['ids'], limit=500)


class CheckResultForm(forms.Form):
    # The new group key checked, as the role named it.
    new_public_key = Base64BytesField(PUBLIC_KEY_BYTES)
    result = forms.ChoiceField(choices=[('passed', 'passed'), ('failed', 'failed')])
    failed_items = forms.CharField(required=False)

    def clean_failed_items(self):
        return item_ids(self.cleaned_data['failed_items'])


class ExposureReportForm(LoginPasswordForm, KeySetupForm):
    """A report that the key passphrase or recovery key may have been seen (design §5.6): the new
    key the browser made, as at setup; the old key's public half, naming the key it replaces;
    each label's data key resealed to the new key, by case id, and each message's, by message id;
    and the ids of the labels and messages it could not open."""

    old_public_key = Base64BytesField(PUBLIC_KEY_BYTES)
    # Optional only so that a lawyer without labels or messages can send an empty object.
    resealed_labels = SealedByIdField(SEALED_DATA_KEY_BYTES, limit=10_000, required=False)
    unreadable_labels = forms.CharField(required=False, widget=forms.HiddenInput)
    # As many as one request carries: Django refuses a body over DATA_UPLOAD_MAX_MEMORY_SIZE
    # (2.5 MB), about 14,000 resealed messages, fewer with labels.
    resealed_messages = SealedByIdField(SEALED_DATA_KEY_BYTES, limit=10_000, uuid_ids=True, required=False)
    unreadable_messages = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_unreadable_labels(self):
        return item_ids(self.cleaned_data['unreadable_labels'])

    def clean_unreadable_messages(self):
        try:
            items = json.loads(self.cleaned_data['unreadable_messages'] or '[]')
        except (ValueError, RecursionError):
            raise forms.ValidationError(_('The key could not be read.')) from None
        if not isinstance(items, list) or len(items) > 10_000 or not all(uuid_id(item) for item in items):
            raise forms.ValidationError(_('The key could not be read.'))
        return [uuid.UUID(item) for item in items]
