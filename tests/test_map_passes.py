"""The map's data for uploaders and code holders (SPEC.md §8, §12 M6.5; ADR 0006's addendum):
served only with a valid map pass, which names no upload and expires with the form's time limit;
never logged, and nothing from elsewhere."""

import logging
import re
import time

import pytest
from django.core import signing
from django.test import Client
from django.urls import reverse

from maps import passes
from tests.test_map_data import TILES, map_dir  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db


def urls(map_pass):
    return [
        reverse('maps:style', kwargs={'map_pass': map_pass}),
        reverse('maps:tiles', kwargs={'map_pass': map_pass}),
        reverse('maps:glyphs', kwargs={'map_pass': map_pass, 'fontstack': 'Noto Sans Regular', 'glyph_range': '0-255'}),
    ]


def issued_minutes_ago(minutes, monkeypatch):
    with monkeypatch.context() as patched:
        patched.setattr(passes.SIGNER, 'timestamp', lambda: signing.b62_encode(int(time.time() - minutes * 60)))
        return passes.issue()


def test_with_a_pass_anyone_gets_the_style_tiles_and_fonts(map_dir):  # noqa: F811
    style_url, tiles_url, glyphs_url = urls(passes.issue())
    client = Client()

    assert client.get(style_url).status_code == 200
    tiles = client.get(tiles_url, HTTP_RANGE='bytes=0-127')
    assert tiles.status_code == 206 and b''.join(tiles.streaming_content) == TILES[:128]
    assert b''.join(client.get(glyphs_url).streaming_content) == b'glyphs'


def test_the_style_points_only_to_this_archive_and_the_same_pass(map_dir):  # noqa: F811
    map_pass = passes.issue()
    style = Client().get(urls(map_pass)[0]).content.decode()

    found = re.findall(r'[a-z]+://[^"\s]+', style)
    assert found and all(url.removeprefix('pmtiles://').startswith(f'http://testserver/map/{map_pass}/') for url in found)


def test_without_a_valid_pass_nothing_is_served(map_dir, monkeypatch, settings):  # noqa: F811
    settings.UNCONFIRMED_ORIGINAL_MINUTES = 120
    good = passes.issue()
    refused = {
        'none': 'map',
        'altered': good[:-1] + ('A' if good[-1] != 'A' else 'B'),
        'from another signer': signing.TimestampSigner(salt='other', sep='.').sign('map'),
        'signing something else': passes.SIGNER.sign('clip-1'),
        'expired': issued_minutes_ago(121, monkeypatch),
    }
    client = Client()

    for reason, map_pass in refused.items():
        for url in urls(map_pass):
            assert client.get(url).status_code == 404, (reason, url)
    assert client.get(urls(issued_minutes_ago(119, monkeypatch))[1]).status_code == 200


def test_a_pass_names_no_upload(map_dir):  # noqa: F811
    # Signed is a constant and the time: the same for every upload in the same second.
    assert passes.SIGNER.unsign(passes.issue()) == passes.SIGNED


def test_requests_with_a_pass_are_never_logged(map_dir, django_log):  # noqa: F811
    map_pass = passes.issue()
    client = Client()
    for url in urls(map_pass):
        client.get(url)
    client.get(urls('forged.pass.here')[1])  # A 404.
    client.get(urls(map_pass)[1], HTTP_RANGE='bytes=5000-6000')  # A 416.
    Client(enforce_csrf_checks=True).post(urls(map_pass)[1])  # Refused by the CSRF check, which logs too.
    server = logging.getLogger('django.server')
    server.info('"%s" %s %s', f'GET /map/{map_pass}/tiles.pmtiles HTTP/1.1', 206, 128)
    # The development server writes the path as sent, which a client may encode.
    server.info('"%s" %s %s', f'GET /%6dap/{map_pass}/style.json HTTP/1.1', 200, 128)

    log = django_log.getvalue()
    assert map_pass not in log and '/map/' not in log
    # Other requests are still logged.
    server.info('"%s" %s %s', 'GET /upload/?next=/map/ HTTP/1.1', 200, 128)
    assert 'GET /upload/?next=/map/' in django_log.getvalue()
