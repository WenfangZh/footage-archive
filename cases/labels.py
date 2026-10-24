"""Case labels, encrypted (docs/m5-encryption-design.md §7.4).

The server encrypts a label like a text, with the case's id as additional data, and seals its
data key to the lawyer's personal key; it sees the label once, in the form that creates the
case. Pages show it through the lawyer's browser, which opens it with the personal key
(keys/static/keys/case_labels.js). A label sealed to a key since retired is unreadable.
"""

import json

from django.db import transaction
from django.views.decorators.debug import sensitive_variables
from nacl.public import PublicKey, SealedBox
from nacl.utils import random

from clips import vault
from keys import grants
from keys.views import b64

LABEL_PARTS = ['label', 'label_ciphertext', 'label_sealed_key', 'label_key', 'label_key_check']


def additional_data(case):
    return f'fa-caselabel-v1:{case.pk}'.encode()


@sensitive_variables('label', 'data_key')
def seal(case, label, lawyer_key):
    """Encrypt `label` as the case's, sealed to `lawyer_key`, replacing what it held."""
    data_key = random(vault.DATA_KEY_BYTES)
    case.label = ''
    case.label_ciphertext = vault.encrypt_text(label, data_key, additional_data(case))
    case.label_sealed_key = SealedBox(PublicKey(bytes(lawyer_key.public_key))).encrypt(data_key)
    case.label_key = lawyer_key
    case.label_key_check = vault.key_check(data_key)
    case.save(update_fields=LABEL_PARTS)


def for_page(case):
    """The label as the lawyer's browser opens it, as JSON; '' if it has none it can open (none
    yet, one from before encryption, or one sealed to a key since retired)."""
    if case.label_ciphertext is None or case.label_key.retired_at is not None:
        return ''
    return json.dumps({
        'ciphertext': b64(case.label_ciphertext), 'additional_data': b64(additional_data(case)),
        'sealed_key': b64(case.label_sealed_key), 'public_key': b64(case.label_key.public_key),
    })


def unreadable(case):
    """Whether the label is sealed to a personal key since retired (design §5.6)."""
    return case.label_ciphertext is not None and case.label_key.retired_at is not None


def encrypt_plaintext_labels():
    """Encrypt the labels of cases made before encryption (design §10), development data only,
    each sealed to its lawyer's current personal key; returns how many were encrypted, and how
    many were left because their lawyer has no key."""
    from .models import Case  # Here: cases.models imports this module.

    encrypted = left = 0
    for case in Case.objects.exclude(label=''):
        with transaction.atomic():
            grants.lock()
            case.refresh_from_db()
            lawyer_key = grants.current_key(case.lawyer_profile)
            if not case.label:
                continue
            if lawyer_key is None:
                left += 1
                continue
            seal(case, case.label, lawyer_key)
            encrypted += 1
    return encrypted, left
