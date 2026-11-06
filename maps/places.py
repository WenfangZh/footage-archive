"""The place lookup (SPEC.md §12 M9, ADR 0011): streets, or addresses with their house numbers,
found by name in the index `fetch_address_data` builds.

Only the words of a query are used, each quoted, so nothing typed can act as search syntax.
Nothing typed is stored or logged here.
"""

import contextlib
import re
import sqlite3
import unicodedata

from . import data

MAX_QUERY = 100
MAX_MATCHES = 10
# Enough candidates for the ordering below to find the best ten among them: this many from
# Paris and as many from elsewhere, so that no Paris match is cut before Paris comes first.
CANDIDATES = 200
# The usual abbreviations, as people type them.
ABBREVIATIONS = {
    'av': 'avenue', 'ave': 'avenue', 'bd': 'boulevard', 'bld': 'boulevard', 'boul': 'boulevard',
    'r': 'rue', 'fg': 'faubourg', 'fbg': 'faubourg', 'pl': 'place', 'imp': 'impasse',
    'all': 'allee', 'ch': 'chemin', 'chem': 'chemin', 'sq': 'square', 'qu': 'quai', 'q': 'quai',
    'rte': 'route', 'crs': 'cours', 'pass': 'passage', 'st': 'saint', 'ste': 'sainte',
}
# What may follow a house number: a street search leaves these out, with the number.
NUMBER_SUFFIXES = {'bis', 'ter', 'quater'}
# Kinds of street: on their own they name no place.
STREET_KINDS = {
    'rue', 'avenue', 'boulevard', 'place', 'impasse', 'allee', 'chemin', 'square', 'quai', 'route',
    'cours', 'passage', 'faubourg', 'villa', 'cite', 'sentier', 'voie', 'rond', 'point',
}
# Words a street's name may hold that nobody needs to type.
SMALL_WORDS = {'de', 'du', 'des', 'la', 'le', 'les', 'l', 'd', 'a', 'au', 'aux', 'et', 'en', 'sur'}


class Unavailable(Exception):
    """The index has not been downloaded."""


def words(text):
    """The words of `text`, without case or accents, abbreviations spelled out."""
    plain = unicodedata.normalize('NFKD', text.lower())
    plain = ''.join(char for char in plain if not unicodedata.combining(char))
    return [ABBREVIATIONS.get(word, word) for word in re.findall(r'\w+', plain)]


def lookup(query, addresses=False):
    """At most ten places for `query`, best first: streets, or, for a lawyer's lookup
    (`addresses`) with a number typed, addresses. An empty query finds nothing."""
    typed = words(query[:MAX_QUERY])
    numbers = [word for word in typed if word.isdigit() and len(word) < 5]
    by_address = addresses and bool(numbers)
    if not by_address:
        # A street has no house number; a five-digit number is a postcode, and stays.
        typed = [word for word in typed if word not in numbers and word not in NUMBER_SUFFIXES]
    # A number, a small word or a kind of street alone names no place, and would match
    # hundreds of thousands of addresses.
    if not set(typed) - set(numbers) - NUMBER_SUFFIXES - SMALL_WORDS - STREET_KINDS:
        return []
    # Each word must appear; the last one may be the start of a word.
    match = ' '.join(f'"{word}"' for word in typed[:-1]) + f' "{typed[-1]}"*'
    if by_address:
        columns, table, make = 'a.number, a.name, a.postcode, a.commune, a.in_paris, a.lat, a.lon', 'addresses', address
    else:
        columns, table, make = 's.name, s.postcodes, s.commune, s.in_paris, s.lat, s.lon, s.south, s.west, s.north, s.east', 'streets', street
    alias = table[0]
    try:
        with contextlib.closing(sqlite3.connect(f'{data.places_file().resolve().as_uri()}?mode=ro', uri=True)) as index:
            found = [
                row
                for in_paris in (1, 0)
                for row in index.execute(
                    f'SELECT {columns} FROM {table}_text t JOIN {table} {alias} ON {alias}.id = t.rowid '
                    f'WHERE {table}_text MATCH ? AND {alias}.in_paris = ? ORDER BY bm25({table}_text) LIMIT ?',
                    (match, in_paris, CANDIDATES),
                )
            ]
    except sqlite3.OperationalError:
        # No index downloaded (or it is being replaced in a way SQLite cannot open).
        raise Unavailable from None
    places = [make(*row) for row in found]
    typed_words = set(typed)
    # A place whose whole name was typed before longer names holding it ("Place de la
    # République" before "… Dominicaine"), the number typed before its "bis", then Paris.
    ranked = sorted(enumerate(places), key=lambda item: (
        not item[1]['name_words'] <= typed_words,
        'number' in item[1] and item[1]['number'] not in numbers,
        not item[1]['in_paris'],
        item[0],
    ))
    return [public(place) for _, place in ranked[:MAX_MATCHES]]


def street(name, postcodes, commune, in_paris, lat, lon, south, west, north, east):
    postcode = postcodes.split()[0]
    return {
        'label': f'{name}, {postcode} {commune}', 'name': name, 'name_words': name_words(name),
        'in_paris': bool(in_paris), 'lat': lat, 'lon': lon, 'bounds': [south, west, north, east],
    }


def address(number, name, postcode, commune, in_paris, lat, lon):
    return {
        'label': f'{number} {name}, {postcode} {commune}', 'name': name, 'name_words': name_words(name),
        'number': number, 'in_paris': bool(in_paris), 'lat': lat, 'lon': lon,
    }


def name_words(name):
    return {word for word in words(name) if word not in SMALL_WORDS}


def public(place):
    """What a match shows: a label and where it is (a street's box too)."""
    return {key: value for key, value in place.items() if key in ('label', 'lat', 'lon', 'bounds')}
