"""The case-area map's page (SPEC.md §12 M4.5, ADR 0006): it loads nothing from another origin.

The page and every file it loads are searched for URLs. Our own files may hold none to
another origin but the credit's link to OpenStreetMap's copyright page, which loads nothing
until the lawyer follows it; the libraries may hold only the ones listed below, which nothing
fetches.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.contrib.staticfiles import finders
from django.test import Client
from django.urls import reverse

from tests.conftest import log_in_with_second_factor
from tests.test_case_pages import make_lawyer

pytestmark = pytest.mark.django_db

# With a scheme, or protocol-relative (//host/...), which also reaches another origin.
URL = re.compile(r"""[a-z][a-z0-9+.-]*://[^\s"'`()<>\\]+|(?<![\w:/])//[\w-]+\.[\w.-]*[a-z]/[^\s"'`()<>\\]*""")
OWN_ORIGIN = 'http://testserver/'
# OpenStreetMap's attribution guidelines ask for this link, for its licence and sources.
OSM_COPYRIGHT = 'https://www.openstreetmap.org/copyright'
MAP_FILES = Path(finders.find('maps/area_map.js')).parent

# Strings in the libraries that look like URLs but are never requested. A library upgrade
# that adds one fails this test, so that someone reads where it is used.
NEVER_FETCHED = {
    # XML namespace names, in SVG icons and in creating elements.
    'http://www.w3.org/1999/xhtml', 'http://www.w3.org/2000/svg',
    # Licence notices and links in comments or developer warnings.
    'https://github.com/maplibre/maplibre-gl-js/blob/v6.15.0/LICENSE.txt',
    'https://github.com/mapbox/mapbox-gl-js/issues/2907',
    'https://github.com/protomaps/PMTiles/tree/main/js',
    # In the error shown when the browser has no WebGL2: text, not a link.
    'https://wiki.openstreetmap.org/wiki/This_map_requires_WebGL',
    # Compared with the origin of messages from MapLibre's worker.
    'resource://android',
    # A base for checking a URL's scheme, never loaded.
    'https://maplibre.invalid/',
    # The links of MapLibre's logo and attribution controls, which the map turns off.
    'https://maplibre.org/',
}


class Loads(HTMLParser):
    """The files a page loads (its scripts, stylesheets and images), and its links."""

    def __init__(self):
        super().__init__()
        self.paths = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a':
            self.links.append(attrs)
        if tag in ('script', 'img') and attrs.get('src'):
            self.paths.append(attrs['src'])
        if tag == 'link' and attrs.get('href'):
            self.paths.append(attrs['href'])


@pytest.fixture
def page():
    client = log_in_with_second_factor(Client(), make_lawyer('lawyer@example.org').user)
    response = client.get(reverse('lawyer:new_case'))
    return client, response


def foreign(text):
    return {url for url in URL.findall(text) if not url.removeprefix('pmtiles://').startswith(OWN_ORIGIN)}


def test_the_map_page_and_what_it_loads_hold_no_url_to_another_origin(page):
    client, response = page
    html = response.content.decode()
    parser = Loads()
    parser.feed(html)
    # The credit's link is the only URL to another origin, and only as a link.
    assert foreign(html) == {OSM_COPYRIGHT} and html.count(OSM_COPYRIGHT) == 1
    elsewhere = [link.get('href', '') for link in parser.links if not link.get('href', '').startswith(('/', '#'))]
    assert elsewhere == [OSM_COPYRIGHT]
    loaded = {path for path in parser.paths if path.startswith('/static/maps/')}
    assert len(loaded) == len(parser.paths) == 5
    # The files these load in turn (MapLibre's module and its worker) are in the same folder.
    for path in MAP_FILES.rglob('*'):
        if path.suffix in ('.js', '.mjs', '.css'):
            found = foreign(path.read_text(encoding='utf-8'))
            if 'vendor' in path.parts:
                assert found <= NEVER_FETCHED, path
            else:
                assert found == set(), path
    style = client.get(reverse('lawyer:map_style')).content.decode()
    assert foreign(style) == set()


def test_the_browser_is_told_to_load_nothing_from_another_origin(page):
    _, response = page
    policy = [part.strip() for part in response['Content-Security-Policy'].split(';')]
    assert "default-src 'self'" in policy and "frame-ancestors 'none'" in policy
    assert not any("'unsafe-inline'" in part or "'unsafe-eval'" in part or '*' in part for part in policy)


def test_the_credit_to_openstreetmap_links_to_its_licence(page):
    _, response = page
    parser = Loads()
    parser.feed(response.content.decode())
    (credit,) = [link for link in parser.links if link.get('href') == OSM_COPYRIGHT]
    # A new tab keeps the form as it is, and OpenStreetMap is not told which page sent the lawyer.
    assert credit['target'] == '_blank' and set(credit['rel'].split()) >= {'noopener', 'noreferrer'}
    assert re.search(
        r'<p class="area-map-attribution">© <a [^>]*href="' + re.escape(OSM_COPYRIGHT) + r'"[^>]*>OpenStreetMap</a> contributors</p>',
        response.content.decode(),
    )
