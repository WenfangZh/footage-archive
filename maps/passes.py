"""Map passes (SPEC.md §8, ADR 0006's addendum of 2026-10-31): how an uploader or code holder,
who has no account, loads the map data.

A pass is a signed time stamp and nothing else. It names no upload, so nothing the server holds,
even within a request, ties the map requests made with it to a clip. It expires with the form's
time limit, and a pass from any other signer, or altered, is refused.
"""

import datetime

from django.conf import settings
from django.core import signing

# '.' rather than ':' as the separator: the pass is one segment of a path.
SIGNER = signing.TimestampSigner(salt='maps.passes', sep='.')
SIGNED = 'map'


def issue():
    return SIGNER.sign(SIGNED)


def is_valid(map_pass):
    lifetime = datetime.timedelta(minutes=settings.UNCONFIRMED_ORIGINAL_MINUTES)
    try:
        return SIGNER.unsign(map_pass, max_age=lifetime) == SIGNED
    except signing.BadSignature:  # Also when it has expired.
        return False
