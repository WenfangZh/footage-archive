"""The map's data (SPEC.md §12 M4.5, ADR 0006): only for lawyers, never logged, nothing from elsewhere.

That nobody but a lawyer gets it is checked with every other lawyer page, in
tests/test_roles.py, since its routes are under the lawyer's prefix.
"""

import logging
import re

import pytest
from django.test import Client
from django.urls import reverse

from audit.models import AuditEntry
from tests.conftest import log_in_with_second_factor
from tests.test_case_pages import make_lawyer

pytestmark = pytest.mark.django_db

TILES = bytes(range(256)) * 4


@pytest.fixture
def map_dir(settings, tmp_path):
    settings.MAP_DIR = tmp_path
    (tmp_path / 'tiles.pmtiles').write_bytes(TILES)
    (tmp_path / 'fonts' / 'Noto Sans Regular').mkdir(parents=True)
    (tmp_path / 'fonts' / 'Noto Sans Regular' / '0-255.pbf').write_bytes(b'glyphs')
    return tmp_path


@pytest.fixture
def lawyer_client():
    return log_in_with_second_factor(Client(), make_lawyer('lawyer@example.org').user)


def glyphs_url(font, glyph_range):
    return reverse('lawyer:map_glyphs', kwargs={'fontstack': font, 'glyph_range': glyph_range})


def test_the_map_data_is_on_the_lawyer_s_pages():
    for url in (reverse('lawyer:map_style'), reverse('lawyer:map_tiles'), glyphs_url('Noto Sans Regular', '0-255')):
        assert url.startswith('/lawyer/map/')


def test_a_lawyer_reads_the_tiles_in_byte_ranges(map_dir, lawyer_client):
    response = lawyer_client.get(reverse('lawyer:map_tiles'), HTTP_RANGE='bytes=0-127')
    assert response.status_code == 206 and b''.join(response.streaming_content) == TILES[:128]
    assert response['Content-Range'] == f'bytes 0-127/{len(TILES)}'


def test_a_lawyer_gets_the_fonts_the_style_names_and_nothing_else(map_dir, lawyer_client):
    response = lawyer_client.get(glyphs_url('Noto Sans Regular', '0-255'))
    assert response.status_code == 200 and b''.join(response.streaming_content) == b'glyphs'
    (map_dir / 'secret.pbf').write_bytes(b'not a font')
    for font, glyph_range in [('Noto Sans Bold', '0-255'), ('..', 'secret'), ('Noto Sans Regular', '..')]:
        assert lawyer_client.get(glyphs_url(font, glyph_range)).status_code == 404


def test_the_style_points_only_to_this_archive(map_dir, lawyer_client):
    style = lawyer_client.get(reverse('lawyer:map_style')).content.decode()
    urls = re.findall(r'[a-z]+://[^"\s]+', style)
    assert urls and all(url.removeprefix('pmtiles://').startswith('http://testserver/lawyer/map/') for url in urls)


def test_requests_for_map_data_are_never_logged(map_dir, lawyer_client, caplog):
    caplog.set_level(logging.DEBUG)
    lawyer_client.get(reverse('lawyer:map_style'))
    lawyer_client.get(reverse('lawyer:map_tiles'), HTTP_RANGE='bytes=0-127')
    lawyer_client.get(reverse('lawyer:map_tiles'), HTTP_RANGE='bytes=5000-6000')  # A 416.
    lawyer_client.get(glyphs_url('Noto Sans Regular', '65280-65535'))  # A 404.
    # As the development server logs each request it answers.
    logging.getLogger('django.server').info('"%s" %s %s', 'GET /lawyer/map/tiles.pmtiles HTTP/1.1', 206, 128)
    assert '/lawyer/map/' not in caplog.text and not AuditEntry.objects.exists()
    logging.getLogger('django.server').info('"%s" %s %s', 'GET /lawyer/ HTTP/1.1', 200, 128)
    assert 'GET /lawyer/ ' in caplog.text


def test_without_map_data_the_map_finds_nothing(settings, tmp_path, lawyer_client):
    settings.MAP_DIR = tmp_path
    assert lawyer_client.get(reverse('lawyer:map_tiles')).status_code == 404
