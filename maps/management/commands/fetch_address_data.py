"""Download the Base Adresse Nationale for Île-de-France and build the place lookup's index
(SPEC.md §12 M9, ADR 0011).

The BAN (Licence Ouverte 2.0) is published as one CSV file per département. The index is one
SQLite file: the streets, each standing for the address point nearest its centre with the box
around all its addresses, for uploaders and code holders; and the addresses with their house
numbers, for lawyers; each with a full-text index. It is built in a folder of its own and
renamed into place, so a lookup never meets a half-built one, and a run cut short, or run twice
at once, keeps a whole one.
"""

import csv
import email.utils
import gzip
import http.client
import math
import os
import shutil
import sqlite3
import tempfile
import urllib.request
import zlib

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from maps import data

FILE_URL = 'https://adresse.data.gouv.fr/data/ban/adresses/latest/csv/adresses-{}.csv.gz'
# The eight départements of Île-de-France, the area the map covers.
DEPARTEMENTS = ('75', '77', '78', '91', '92', '93', '94', '95')
HEADERS = {'User-Agent': 'footage-archive fetch_address_data'}
# Each département's file is a few MB to about 20 MB: far more is not the BAN.
MAX_FILE_BYTES = 300 * 1000 * 1000
# Numbers the BAN gives places that have none: such a row is part of its street, not an address.
NO_NUMBER = {'9999', '99999'}
# The columns read, by their names in the BAN's header.
COLUMNS = ('numero', 'rep', 'nom_voie', 'code_postal', 'code_insee', 'nom_commune', 'lon', 'lat')
# Downloaded data is checked like any other input: within these bounds and lengths, or skipped.
WEST, SOUTH, EAST, NORTH = 1.4, 48.1, 3.6, 49.3
MAX_TEXT = 200
# The tokenizer forgives case and accents (remove_diacritics 2 covers all of Latin script).
TOKENIZE = "unicode61 remove_diacritics 2"

SCHEMA = f"""
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE streets (
    -- postcodes: every postcode the street's addresses have, the first one shown.
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, postcodes TEXT NOT NULL, commune TEXT NOT NULL,
    in_paris INTEGER NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
    south REAL NOT NULL, west REAL NOT NULL, north REAL NOT NULL, east REAL NOT NULL
);
CREATE VIRTUAL TABLE streets_text USING fts5(name, postcodes, commune, content='streets', content_rowid='id', tokenize='{TOKENIZE}');
CREATE TABLE addresses (
    id INTEGER PRIMARY KEY, number TEXT NOT NULL, name TEXT NOT NULL, postcode TEXT NOT NULL,
    commune TEXT NOT NULL, in_paris INTEGER NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL
);
CREATE VIRTUAL TABLE addresses_text USING fts5(number, name, postcode, commune, content='addresses', content_rowid='id', tokenize='{TOKENIZE}');
"""


class Command(BaseCommand):
    help = "Download the Base Adresse Nationale for Île-de-France and build the place lookup's index."

    def handle(self, *args, **options):
        settings.PLACES_DIR.mkdir(mode=0o700, exist_ok=True)
        settings.PLACES_DIR.chmod(0o700)
        # One folder per run, for the downloads and the index being built: two runs never share
        # a file, and whatever a run leaves behind goes with its folder.
        work = tempfile.mkdtemp(dir=settings.PLACES_DIR, prefix='build.')
        try:
            files, published = {}, []
            for departement in DEPARTEMENTS:
                files[departement], date = download(departement, work)
                published.append(date)
                self.stdout.write(f'Downloaded département {departement}.')
            # The oldest file's date: the data is at least that recent.
            streets, addresses = build_index(files, min(published), os.path.join(work, 'places.sqlite3'))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        self.stdout.write(self.style.SUCCESS(f'Place lookup: {streets} streets, {addresses} addresses, published {min(published)}.'))


def download(departement, folder):
    """One département's file, and the date it was published (YYYY-MM-DD, from Last-Modified)."""
    url = FILE_URL.format(departement)
    target = os.path.join(folder, f'{departement}.csv.gz')
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=120) as response:
            # A redirect must not leave HTTPS.
            if not response.geturl().startswith('https://'):
                raise CommandError(f'{url} redirected away from HTTPS: the old index, if any, is kept.')
            modified = response.headers.get('Last-Modified')
            size = 0
            with open(target, 'wb') as file:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise CommandError(f'{url} is larger than expected: the old index, if any, is kept.')
                    file.write(chunk)
            length = response.headers.get('Content-Length')
            if length is not None and length.isdigit() and int(length) != size:
                raise CommandError(f'{url} arrived incomplete: the old index, if any, is kept.')
    except (OSError, http.client.HTTPException) as error:
        raise CommandError(f'Could not download {url} ({error}): the old index, if any, is kept.') from None
    try:
        published = email.utils.parsedate_to_datetime(modified).date().isoformat()
    except (TypeError, ValueError):
        raise CommandError(f'{url} gave no readable publication date: the old index, if any, is kept.') from None
    return target, published


def rows(path):
    """The usable rows of one département's file, as checked values."""
    try:
        yield from read_rows(path)
    except (OSError, EOFError, zlib.error, UnicodeDecodeError, csv.Error) as error:
        raise CommandError(f'{os.path.basename(path)} could not be read ({error}): the old index, if any, is kept.') from None


def read_rows(path):
    with gzip.open(path, 'rt', encoding='utf-8', newline='') as file:
        reader = csv.DictReader(file, delimiter=';')
        if reader.fieldnames is None or not set(COLUMNS) <= set(reader.fieldnames):
            raise CommandError(f'{os.path.basename(path)} lacks the expected columns: the old index, if any, is kept.')
        for row in reader:
            try:
                lat, lon = float(row['lat']), float(row['lon'])
            except (TypeError, ValueError):
                continue
            name, commune = row['nom_voie'].strip(), row['nom_commune'].strip()
            # "12 bis", as it is written and typed; no number for the BAN's placeholders.
            number = row['numero'].strip()
            number = '' if number in NO_NUMBER else ' '.join(filter(None, [number, row['rep'].strip()]))
            postcode, insee = row['code_postal'].strip(), row['code_insee'].strip()
            if not (SOUTH <= lat <= NORTH and WEST <= lon <= EAST) or not name or not commune:
                continue
            if any(len(text) > MAX_TEXT for text in (name, commune, number, postcode, insee)):
                continue
            yield number, name, postcode, insee, commune, lat, lon


def build_index(files, published, partial):
    """Build the index from the downloaded files into `partial`, beside them, then rename it into
    place; return the counts kept."""
    database = sqlite3.connect(partial)
    try:
        # Nothing to recover if the build is cut short: the file is thrown away with its folder.
        database.executescript('PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF;' + SCHEMA)
        streets = {}
        address_count = 0
        with database:
            for departement in sorted(files):
                before = len(streets)
                for number, name, postcode, insee, commune, lat, lon in rows(files[departement]):
                    in_paris = int(insee.startswith('75'))
                    if number:
                        database.execute(
                            'INSERT INTO addresses (number, name, postcode, commune, in_paris, lat, lon) VALUES (?, ?, ?, ?, ?, ?, ?)',
                            (number, name, postcode, commune, in_paris, lat, lon),
                        )
                        address_count += 1
                    street = streets.setdefault((insee, name), {'postcodes': [], 'commune': commune, 'in_paris': in_paris, 'points': []})
                    if postcode not in street['postcodes']:
                        street['postcodes'].append(postcode)
                    street['points'].append((lat, lon))
                # A département with no street would drop out of the index unnoticed.
                if len(streets) == before:
                    raise CommandError(f'Département {departement} held no usable address: the old index, if any, is kept.')
            for (_, name), street in streets.items():
                points = street['points']
                lat, lon = middle(points)
                lats, lons = [p[0] for p in points], [p[1] for p in points]
                database.execute(
                    'INSERT INTO streets (name, postcodes, commune, in_paris, lat, lon, south, west, north, east) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (name, ' '.join(street['postcodes']), street['commune'], street['in_paris'], lat, lon, min(lats), min(lons), max(lats), max(lons)),
                )
            database.execute("INSERT INTO streets_text (streets_text) VALUES ('rebuild')")
            database.execute("INSERT INTO addresses_text (addresses_text) VALUES ('rebuild')")
            database.execute("INSERT INTO meta (key, value) VALUES ('published', ?)", (published,))
    finally:
        database.close()
    os.replace(partial, data.places_file())
    return len(streets), address_count


def middle(points):
    """The point nearest the centre of `points`: one that lies on the street, unlike the centre."""
    lat = sum(p[0] for p in points) / len(points)
    lon = sum(p[1] for p in points) / len(points)
    scale = math.cos(math.radians(lat))
    return min(points, key=lambda p: (p[0] - lat) ** 2 + ((p[1] - lon) * scale) ** 2)
