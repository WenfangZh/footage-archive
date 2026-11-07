"""The place lookup in a form, without JavaScript (SPEC.md §12 M9, ADR 0011): the form's "Look up"
button sends it back to its own page, which lists the matches; choosing one sends it back once
more, with the match's point as the form's typed latitude and longitude. Neither ever saves the
form. The upload form and the code holder's page look up streets; a lawyer's new case, addresses.

What is typed is never stored or logged here; the views hide it from error reports.
"""

from django.conf import settings
from django.forms.utils import ErrorDict

from clips import rate_limits

from . import data, places


def run(request, addresses):
    """For a form sent back by "Look up" or by a match's button: what the page shows of the
    lookup, and the form's data, a chosen match's point filled in as its latitude and longitude.
    (None, the data) for a form sent to be saved."""
    if 'choose' not in request.POST and 'lookup' not in request.POST:
        return None, request.POST
    query = request.POST.get('place', '').strip()
    found = {'query': query}
    if 'choose' in request.POST:
        point = chosen_point(request.POST['choose'])
        if point is None:  # Not a match the page offered: shown again as sent.
            return found, request.POST
        data = request.POST.copy()
        data['latitude'], data['longitude'] = point
        return {**found, 'chosen': True}, data
    if rate_limits.is_limited(request, rate_limits.PLACE_LOOKUPS):
        return {**found, 'problem': 'too_many'}, request.POST
    rate_limits.count(request, rate_limits.PLACE_LOOKUPS)
    if not query:
        return {**found, 'problem': 'empty'}, request.POST
    if len(query) > places.MAX_QUERY:
        return {**found, 'problem': 'too_long'}, request.POST
    try:
        matches = places.lookup(query, addresses=addresses)
    except places.Unavailable:
        return {**found, 'problem': 'unavailable'}, request.POST
    if not matches:
        return {**found, 'problem': 'none_found'}, request.POST
    return {**found, 'matches': matches}, request.POST


def chosen_point(value):
    """A match's point as two strings, latitude and longitude, or None. Sent back by the page, it
    is checked as typed coordinates are (and is no more than they could be)."""
    try:
        latitude, longitude = (float(part) for part in value.split())
    except ValueError:
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):  # Also refuses nan.
        return None
    return str(latitude), str(longitude)


def unchecked(form):
    """`form`, to render again as sent, without its errors: a lookup saves nothing, so it has
    nothing to say about the rest of the form. For rendering only: never validate or save it."""
    form._errors = ErrorDict()
    return form


def respond(shown, response):
    """Too many lookups are answered as the map's lookup route answers them."""
    if shown.get('problem') == 'too_many':
        response.status_code = 429
        response['Retry-After'] = str(settings.RATE_LIMIT_WINDOW_MINUTES * 60)
    return response


def page_context(lookup_url, addresses):
    """What the lookup box needs on a page (maps/place_lookup.html): without the downloaded
    data, it says the lookup is unavailable."""
    return {
        'places_available': data.places_file().exists(),
        'place_max_length': places.MAX_QUERY,
        'place_lookup_url': lookup_url,
        'place_lookup_addresses': addresses,
    }
