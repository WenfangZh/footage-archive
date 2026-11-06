"""The place lookup (SPEC.md §12 M9, ADR 0011): streets, or addresses for a lawyer, found
despite accents, case and abbreviations, best first; behind a map pass or a lawyer, as the map
data is; never logged or kept."""

import gzip
import logging

import pytest
from django.test import Client
from django.urls import reverse

from audit.models import AuditEntry
from maps import passes, places
from maps.management.commands.fetch_address_data import COLUMNS, build_index
from tests.conftest import database_dump, log_in_with_second_factor, make_lawyer

pytestmark = pytest.mark.django_db

# Made-up rows in the BAN's layout: number, name, postcode, INSEE code, commune, longitude, latitude.
ROWS = [
    ('1', 'Boulevard Saint-Germain', '75005', '75105', 'Paris 5e Arrondissement', 2.3500, 48.8500),
    ('2', 'Place de la République Dominicaine', '75017', '75117', 'Paris 17e Arrondissement', 2.3080, 48.8800),
    ('2', 'Place de la République', '75011', '75111', 'Paris 11e Arrondissement', 2.3640, 48.8670),
    ('2 bis', 'Place de la République', '75011', '75111', 'Paris 11e Arrondissement', 2.3641, 48.8671),
    ('5', 'Rue de la Paix', '75002', '75102', 'Paris 2e Arrondissement', 2.3310, 48.8690),
    ('5', 'Rue de la Paix', '77760', '77478', 'Ury', 2.6000, 48.3400),
    ('3', 'Avenue des Champs-Élysées', '75008', '75108', 'Paris 8e Arrondissement', 2.3070, 48.8700),
    # Outside Paris, the longer name in the shorter commune, which the search alone would put first.
    ('4', 'Rue Victor Hugo', '78100', '78551', 'Saint-Germain-en-Laye', 2.0900, 48.9000),
    ('4', 'Rue Victor Hugo Prolongée', '77760', '77478', 'Ury', 2.6010, 48.3410),
    *[(str(n), f'Rue Test {n}', '75001', '75101', 'Paris 1er Arrondissement', 2.34, 48.86) for n in range(1, 16)],
]


@pytest.fixture
def index(settings, tmp_path):
    settings.PLACES_DIR = tmp_path / 'places'
    settings.PLACES_DIR.mkdir()
    files = {}
    for departement in ('75', '77', '78'):
        path = tmp_path / f'{departement}.csv.gz'
        with gzip.open(path, 'wt', encoding='utf-8') as file:
            file.write(';'.join(COLUMNS) + '\n')
            for number, name, postcode, insee, commune, lon, lat in ROWS:
                if insee.startswith(departement):
                    row = dict.fromkeys(COLUMNS, '')
                    row.update(numero=number.split()[0], rep=' '.join(number.split()[1:]), nom_voie=name,
                               code_postal=postcode, code_insee=insee, nom_commune=commune, lon=str(lon), lat=str(lat))
                    file.write(';'.join(row[column] for column in COLUMNS) + '\n')
        files[departement] = path
    build_index(files, '2026-11-05', tmp_path / 'building.sqlite3')


def labels(query, addresses=False):
    return [match['label'] for match in places.lookup(query, addresses=addresses)]


def test_a_street_is_found_despite_accents_case_and_abbreviations(index):
    assert labels('BD ST GERMAIN') == ['Boulevard Saint-Germain, 75005 Paris 5e Arrondissement']
    assert labels('av champs elysees') == ['Avenue des Champs-Élysées, 75008 Paris 8e Arrondissement']
    # The last word may be the start of one.
    assert labels('champs ely') == ['Avenue des Champs-Élysées, 75008 Paris 8e Arrondissement']


def test_the_whole_name_typed_and_paris_come_first(index):
    assert labels('place republique')[0] == 'Place de la République, 75011 Paris 11e Arrondissement'
    assert labels('rue de la paix') == ['Rue de la Paix, 75002 Paris 2e Arrondissement', 'Rue de la Paix, 77760 Ury']
    assert labels('rue victor hugo') == ['Rue Victor Hugo, 78100 Saint-Germain-en-Laye', 'Rue Victor Hugo Prolongée, 77760 Ury']


def test_at_most_ten_matches(index):
    assert len(labels('rue test')) == 10


def test_a_lawyer_finds_house_numbers_and_an_uploader_streets_only(index):
    assert labels('2 place republique', addresses=True)[:2] == [
        '2 Place de la République, 75011 Paris 11e Arrondissement',
        '2 bis Place de la République, 75011 Paris 11e Arrondissement',
    ]
    # An uploader's lookup finds streets, the number typed left out.
    streets = labels('2 place republique')
    assert streets[0] == 'Place de la République, 75011 Paris 11e Arrondissement'
    assert not any(label[0].isdigit() for label in streets)
    match = places.lookup('place republique')[0]
    assert set(match) == {'label', 'lat', 'lon', 'bounds'}


@pytest.fixture
def lawyer_client(client):
    return log_in_with_second_factor(client, make_lawyer().user)


def pass_url():
    return reverse('maps:lookup', kwargs={'map_pass': passes.issue()})


def test_the_routes_answer_a_post_with_a_pass_or_for_a_lawyer(index, lawyer_client):
    uploader = Client().post(pass_url(), {'place': 'bd st germain'})
    assert uploader.json() == {'matches': places.lookup('bd st germain')}
    assert lawyer_client.post(reverse('lawyer:map_lookup'), {'place': '2 place republique'}).json()['matches'][0]['label'].startswith('2 Place')
    # A forged pass finds nothing; neither does a GET, which would put the query in a URL.
    assert Client().post(reverse('maps:lookup', kwargs={'map_pass': 'forged'}), {'place': 'paix'}).status_code == 404
    assert Client().get(pass_url(), {'place': 'paix'}).status_code == 405
    assert lawyer_client.get(reverse('lawyer:map_lookup'), {'place': 'paix'}).status_code == 405


def test_an_over_long_query_and_too_many_lookups_are_refused(index, settings):
    settings.RATE_LIMIT_PLACE_LOOKUPS = 2
    client = Client()
    assert client.post(pass_url(), {'place': 'x' * 101}).json() == {'error': 'too_long'}
    assert client.post(pass_url(), {'place': 'paix'}).status_code == 200
    refused = client.post(pass_url(), {'place': 'paix'})
    assert refused.status_code == 429 and refused.json() == {'error': 'too_many'}


def test_without_the_data_the_lookup_says_it_is_unavailable(settings, tmp_path):
    settings.PLACES_DIR = tmp_path
    response = Client().post(pass_url(), {'place': 'paix'})
    assert response.status_code == 503 and response.json() == {'error': 'unavailable'}


def test_a_query_is_never_logged_kept_or_shown_in_error_reports(index, lawyer_client, django_log):
    before = database_dump()
    response = lawyer_client.post(reverse('lawyer:map_lookup'), {'place': '5 rue de la paix'})
    Client().post(pass_url(), {'place': 'rue de la paix'})
    # As the development server logs each request it answers.
    logging.getLogger('django.server').info('"%s" %s %s', 'POST /lawyer/map/places/ HTTP/1.1', 200, 128)
    assert 'places' not in django_log.getvalue() and 'paix' not in django_log.getvalue()
    assert not AuditEntry.objects.exists() and 'paix' not in database_dump() and database_dump() == before
    # Error reports hide the field (Django's SafeExceptionReporterFilter reads this).
    assert response.wsgi_request.sensitive_post_parameters == ('place',)
