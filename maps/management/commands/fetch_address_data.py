"""Download the Base Adresse Nationale for Île-de-France and build the place lookup's index
(SPEC.md §12 M9, ADR 0011).

The BAN (Licence Ouverte 2.0) is published as one CSV file per département. The index is one
SQLite file: the streets, each standing for the address point nearest its centre with the box
around all its addresses, for uploaders and code holders; and the addresses with their house
numbers, for lawyers; each with a full-text index. It is built under another name and renamed
into place, so a lookup never meets a half-built one, and a run cut short keeps the old one.
"""

import csv
import email.utils
import gzip
import math
import os
import shutil
import sqlite3
import tempfile
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from maps import data

FILE_URL = 'https://adresse.data.gouv.fr/data/ban/adresses/latest/csv/adresses-{}.csv.gz'
# The eight départements of Île-de-France, the area the map covers.
DEPARTEMENTS = ('75', '77', '78', '91', '92', '93', '94', '95')
HEADERS = {'User-Agent': 'footage-archive fetch_address_data'}
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
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, postcode TEXT NOT NULL, commune TEXT NOT NULL,
    in_paris INTEGER NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
    south REAL NOT NULL, west REAL NOT NULL, north REAL NOT NULL, east REAL NOT NULL
);
CREATE VIRTUAL TABLE streets_text USING fts5(name, postcode, commune, content='streets', content_rowid='id', tokenize='{TOKENIZE}');
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
        downloads = tempfile.mkdtemp(dir=settings.PLACES_DIR, prefix='download.')
        try:
            files, published = {}, []
            for departement in DEPARTEMENTS:
                files[departement], date = download(departement, downloads)
                published.append(date)
                self.stdout.write(f'Downloaded département {departement}.')
            streets, addresses = build_index(files, max(published), data.places_file())
        finally:
            shutil.rmtree(downloads, ignore_errors=True)
        self.stdout.write(self.style.SUCCESS(f'Place lookup: {streets} streets, {addresses} addresses, published {max(published)}.'))


def download(departement, folder):
    """One département's file, and the date it was published (YYYY-MM-DD, from Last-Modified)."""
    url = FILE_URL.format(departement)
    target = os.path.join(folder, f'{departement}.csv.gz')
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=120) as response:
            modified = response.headers.get('Last-Modified')
            with open(target, 'wb') as file:
                shutil.copyfileobj(response, file)
    except OSError as error:
        raise CommandError(f'Could not download {url} ({error}): the old index, if any, is kept.') from None
    published = email.utils.parsedate_to_datetime(modified).date().isoformat() if modified else None
    if published is None:
        raise CommandError(f'{url} gave no publication date: the old index, if any, is kept.')
    return target, published


def rows(path):
    """The usable rows of one département's file, as checked values."""
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
            number = (row['numero'].strip() + row['rep'].strip()).strip()
            postcode, insee = row['code_postal'].strip(), row['code_insee'].strip()
            if not (SOUTH <= lat <= NORTH and WEST <= lon <= EAST) or not name or not commune:
                continue
            if any(len(text) > MAX_TEXT for text in (name, commune, number, postcode, insee)):
                continue
            yield number, name, postcode, insee, commune, lat, lon


def build_index(files, published, target):
    """Build the index from the downloaded files into `target`; return the counts kept."""
    partial = target.with_suffix('.partial')
    partial.unlink(missing_ok=True)
    database = sqlite3.connect(partial)
    try:
        database.executescript(SCHEMA)
        streets = {}
        address_count = 0
        with database:
            for departement in sorted(files):
                for number, name, postcode, insee, commune, lat, lon in rows(files[departement]):
                    in_paris = int(insee.startswith('75'))
                    if number:
                        database.execute(
                            'INSERT INTO addresses (number, name, postcode, commune, in_paris, lat, lon) VALUES (?, ?, ?, ?, ?, ?, ?)',
                            (number, name, postcode, commune, in_paris, lat, lon),
                        )
                        address_count += 1
                    street = streets.setdefault((insee, name), {'postcode': postcode, 'commune': commune, 'in_paris': in_paris, 'points': []})
                    street['points'].append((lat, lon))
            for (_, name), street in streets.items():
                points = street['points']
                lat, lon = middle(points)
                lats, lons = [p[0] for p in points], [p[1] for p in points]
                database.execute(
                    'INSERT INTO streets (name, postcode, commune, in_paris, lat, lon, south, west, north, east) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (name, street['postcode'], street['commune'], street['in_paris'], lat, lon, min(lats), min(lons), max(lats), max(lons)),
                )
            database.execute("INSERT INTO streets_text (streets_text) VALUES ('rebuild')")
            database.execute("INSERT INTO addresses_text (addresses_text) VALUES ('rebuild')")
            database.executemany('INSERT INTO meta (key, value) VALUES (?, ?)', [
                ('published', published), ('departements', ','.join(sorted(files))),
            ])
        database.execute('VACUUM')
    finally:
        database.close()
    if not streets:
        partial.unlink(missing_ok=True)
        raise CommandError('The files held no usable address: the old index, if any, is kept.')
    os.replace(partial, target)
    return len(streets), address_count


def middle(points):
    """The point nearest the centre of `points`: one that lies on the street, unlike the centre."""
    lat = sum(p[0] for p in points) / len(points)
    lon = sum(p[1] for p in points) / len(points)
    scale = math.cos(math.radians(lat))
    return min(points, key=lambda p: (p[0] - lat) ** 2 + ((p[1] - lon) * scale) ** 2)
