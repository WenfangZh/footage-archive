"""Upload codes (SPEC.md §5, ADR 0001).

A code is 28 random characters in Crockford's base32 (140 bits), shown in 7 groups
of 4. The server never stores a code, only its keyed hash: HMAC-SHA-256 with
UPLOAD_CODE_KEY. Without that key, a copy of the database cannot be used to test
guesses, and with it a presented code can still be looked up.
"""

import hashlib
import hmac
import secrets

from django.conf import settings

# Crockford's base32: digits and capital letters, without I, L, O and U.
ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
LENGTH = 28
GROUP_SIZE = 4

# Characters people may type for the ones that look alike, as Crockford's base32 allows.
LOOKALIKES = str.maketrans({'O': '0', 'I': '1', 'L': '1'})
SEPARATORS = str.maketrans('', '', '- \t\n')


def generate():
    """A new upload code, in its normalised form (no separators)."""
    return ''.join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def display(code):
    """A normalised code in groups for the code card, e.g. '7K3Q-M9XA-…'."""
    return '-'.join(code[i:i + GROUP_SIZE] for i in range(0, len(code), GROUP_SIZE))


def normalise(typed):
    """The normalised form of a code as typed, or None if it cannot be a code.

    Case, dashes and spaces are ignored, and O, I and L are read as 0, 1 and 1.
    """
    code = typed.upper().translate(SEPARATORS).translate(LOOKALIKES)
    if len(code) != LENGTH or any(char not in ALPHABET for char in code):
        return None
    return code


def keyed_hash(code):
    """The keyed hash stored for a normalised code, as 64 hexadecimal characters."""
    key = settings.UPLOAD_CODE_KEY.encode()
    return hmac.new(key, code.encode(), hashlib.sha256).hexdigest()
