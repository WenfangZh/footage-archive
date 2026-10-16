"""Default tags (SPEC.md §8): language-neutral keys with translated tag names.

They describe what can be seen, not conclusions. The keys are stored; the names are
only shown, in the reader's language. Custom tags are stored as typed instead.
"""

from django.utils.translation import gettext_lazy as _

DEFAULT_TAGS = {
    'arrest': _('Arrest'),
    'use_of_force': _('Use of force'),
    'officer_id_legible': _('Officer ID number legible'),
    'officer_id_not_visible': _('Officer ID number not visible'),
    'police_armband_visible': _('Police armband visible'),
    'no_police_armband_visible': _('No police armband visible'),
    'plainclothes_officers': _('Plainclothes officers'),
    'minor_in_frame': _('Minor in frame'),
    'injury_visible': _('Injury visible'),
    'tear_gas': _('Tear gas'),
    'kettling': _('Kettling (crowd encircled and held by police)'),
}

CUSTOM_TAG_MAX_LENGTH = 60
