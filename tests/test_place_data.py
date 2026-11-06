"""The place lookup's data (SPEC.md §12 M9, ADR 0011): `fetch_address_data` builds the index
from the BAN's files, and running it again gives the same index, or a newer one, with no
half-built one ever in use."""

import gzip
import sqlite3

import pytest
from django.core.management import CommandError, call_command

from maps import data
from maps.management.commands import fetch_address_data

HEADER = 'id;id_fantoir;numero;rep;nom_voie;code_postal;code_insee;nom_commune;code_insee_ancienne_commune;nom_ancienne_commune;x;y;lon;lat;type_position'

# Made-up rows in the BAN's layout: a street of three addresses, a place with no number, and
# rows the command must skip (outside the map, no position, a name far too long).
PARIS = [
    ';;10;;Rue de Test;75004;75104;Paris 4e Arrondissement;;;;;2.3500;48.8500;entrée',
    ';;12;bis;Rue de Test;75004;75104;Paris 4e Arrondissement;;;;;2.3510;48.8505;entrée',
    ';;40;;Rue de Test;75004;75104;Paris 4e Arrondissement;;;;;2.3600;48.8550;entrée',
    ';;;;Place Sans Numéro;75004;75104;Paris 4e Arrondissement;;;;;2.3400;48.8400;segment',
    # The BAN's placeholder for a place without a number: part of its street, not an address.
    ';;99999;;Place Sans Numéro;75004;75104;Paris 4e Arrondissement;;;;;2.3402;48.8401;segment',
    ';;1;;Rue Lointaine;75004;75104;Paris 4e Arrondissement;;;;;5.0000;45.0000;entrée',
    ';;2;;Rue Sans Position;75004;75104;Paris 4e Arrondissement;;;;;;;entrée',
    ';;3;;' + 'x' * 300 + ';75004;75104;Paris 4e Arrondissement;;;;;2.3500;48.8500;entrée',
]
NANTERRE = [';;5;;Rue de Test;92000;92050;Nanterre;;;;;2.2000;48.8900;entrée']
# One street in each other département, which the index must not lose.
ELSEWHERE = ';;1;;Grande Rue;{0}000;{0}001;Commune {0};;;;;2.5000;48.7000;entrée'


@pytest.fixture
def ban(settings, tmp_path, monkeypatch):
    """The command's downloads replaced by these files, published on the dates given."""
    settings.PLACES_DIR = tmp_path / 'places'
    files = {departement: [ELSEWHERE.format(departement)] for departement in fetch_address_data.DEPARTEMENTS}
    files['75'], files['92'] = PARIS, NANTERRE
    published = {departement: '2026-11-05' for departement in files}
    published['92'] = '2026-11-01'

    def download(departement, folder):
        if departement not in files:
            raise CommandError('no such file')
        path = f'{folder}/{departement}.csv.gz'
        with gzip.open(path, 'wt', encoding='utf-8') as file:
            file.write('\n'.join([HEADER, *files[departement]]) + '\n')
        return path, published[departement]

    monkeypatch.setattr(fetch_address_data, 'download', download)
    return files, published


def index():
    return sqlite3.connect(data.places_file())


def test_the_index_holds_the_streets_and_the_addresses_with_numbers(ban):
    call_command('fetch_address_data')
    with index() as db:
        streets = db.execute("SELECT name, postcodes, commune, in_paris, lat, lon, south, west, north, east FROM streets WHERE name != 'Grande Rue' ORDER BY name, postcodes").fetchall()
        numbers = sorted(row[0] for row in db.execute('SELECT number FROM addresses'))
        published = dict(db.execute('SELECT key, value FROM meta'))['published']
    # Rue de Test in Paris and in Nanterre are two streets; the skipped rows left nothing.
    assert [(s[0], s[1], s[3]) for s in streets] == [
        ('Place Sans Numéro', '75004', 1), ('Rue de Test', '75004', 1), ('Rue de Test', '92000', 0),
    ]
    paris = streets[1]
    # The address point nearest the centre (2.3537, 48.8518): 12 bis, a point on the street.
    assert paris[4:6] == (48.8505, 2.351)
    assert paris[6:] == (48.85, 2.35, 48.855, 2.36)
    # Addresses keep their numbers as written; a place without one is a street only.
    assert numbers == ['1'] * 6 + ['10', '12 bis', '40', '5']
    # The oldest of the files' dates: the data is at least that recent.
    assert published == '2026-11-01'


def test_running_it_again_builds_a_newer_index_in_one_step(ban):
    files, published = ban
    call_command('fetch_address_data')
    files['92'] = NANTERRE + [';;7;;Avenue Nouvelle;92000;92050;Nanterre;;;;;2.2100;48.8910;entrée']
    published.update(dict.fromkeys(published, '2026-11-06'))
    call_command('fetch_address_data')
    with index() as db:
        assert db.execute("SELECT count(*) FROM streets WHERE name = 'Avenue Nouvelle'").fetchone() == (1,)
        assert dict(db.execute('SELECT key, value FROM meta'))['published'] == '2026-11-06'
    # Nothing half-built or downloaded is left beside it.
    assert sorted(path.name for path in data.places_file().parent.iterdir()) == ['places.sqlite3']


def test_a_run_cut_short_keeps_the_index_in_use(ban):
    files, _ = ban
    call_command('fetch_address_data')
    before = data.places_file().read_bytes()
    del files['94']  # Its download fails.
    with pytest.raises(CommandError):
        call_command('fetch_address_data')
    assert data.places_file().read_bytes() == before
    assert sorted(path.name for path in data.places_file().parent.iterdir()) == ['places.sqlite3']


def test_a_departement_with_no_street_keeps_the_index_in_use(ban):
    files, _ = ban
    call_command('fetch_address_data')
    before = data.places_file().read_bytes()
    files['77'] = []  # A file with its header only.
    with pytest.raises(CommandError):
        call_command('fetch_address_data')
    assert data.places_file().read_bytes() == before
    assert sorted(path.name for path in data.places_file().parent.iterdir()) == ['places.sqlite3']


def test_a_file_without_the_expected_columns_builds_nothing(ban, monkeypatch):
    def download(departement, folder):
        path = f'{folder}/{departement}.csv.gz'
        with gzip.open(path, 'wt', encoding='utf-8') as file:
            file.write('id;name;place\n1;Rue de Test;somewhere\n')
        return path, '2026-11-05'

    monkeypatch.setattr(fetch_address_data, 'download', download)
    with pytest.raises(CommandError):
        call_command('fetch_address_data')
    assert not data.places_file().exists()


def test_the_licences_page_names_the_ban_and_the_data_s_date(ban, client):
    # SPEC.md §12 M9: the Licence Ouverte asks for the source and the date.
    assert 'Not downloaded on this archive' in client.get('/licences/', headers={'accept-language': 'en'}).content.decode()
    call_command('fetch_address_data')
    page = client.get('/licences/', headers={'accept-language': 'en'}).content.decode()
    assert 'Base Adresse Nationale' in page and 'Licence Ouverte 2.0' in page
    assert 'Data of 2026-11-01.' in page
