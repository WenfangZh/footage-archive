"""The licences page: the archive's licence and source, and the notices of what it serves (SPEC.md §12, M7)."""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
from django.urls import reverse

pytestmark = pytest.mark.django_db

ROOT = settings.BASE_DIR
# The notices the archive serves as static files: those of the vendored libraries, the map's style and its fonts.
# Any other .txt in a static folder would count too, and need a link from the page.
NOTICES = {
    # The path below the app's static folder, as the static tag names it.
    path.relative_to(ROOT / path.relative_to(ROOT).parts[0] / 'static').as_posix()
    for path in ROOT.glob('*/static/**/*.txt')
}
# With a scheme, or protocol-relative (//host/...), as in tests/test_map_page.py.
URL = re.compile(r"""[a-z][a-z0-9+.-]*://[^\s"'`()<>\\]+|(?<![\w:/])//[\w-]+\.[\w.-]*[a-z]/[^\s"'`()<>\\]*""")


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.append(dict(attrs))


def links(html):
    parser = Links()
    parser.feed(html)
    return parser.links


def page(client, language):
    response = client.get(reverse('licences'), headers={'accept-language': language})
    assert response.status_code == 200
    return response.content.decode()


def test_the_page_is_open_to_anyone_in_both_languages(client):
    for language, title in (('fr', 'Licences et code source'), ('en', 'Licences and source code')):
        html = page(client, language)
        assert title in html
        # The archive's licence, the map data's and the fonts'.
        for name in ('GNU Affero General Public License', 'Open Database License (ODbL)', 'SIL Open Font License'):
            assert name in html, (language, name)


def test_every_page_links_to_it(client):
    for name in ('home', 'clips:upload', 'clips:code_holder', 'accounts:login'):
        hrefs = [link['href'] for link in links(client.get(reverse(name)).content.decode())]
        assert reverse('licences') in hrefs, name


def test_it_offers_the_source_of_the_running_code(client, settings):
    settings.SOURCE_URL = 'https://code.example.org/archive'
    (source,) = [link for link in links(page(client, 'en')) if link['href'] == settings.SOURCE_URL]
    # The source's host is not told which archive sent the visitor.
    assert 'noreferrer' in source['rel']


def test_it_links_to_every_notice_the_archive_serves(client):
    assert {'maps/style.LICENSE.txt', 'maps/fonts.OFL.txt', 'clips/vendor/tus-js-client.bundled.LICENSES.txt'} <= NOTICES
    linked = {link['href'].removeprefix('/static/') for link in links(page(client, 'en'))}
    assert NOTICES <= linked
    for notice in NOTICES:
        # Static files are served with no charset, so a browser could garble anything beyond ASCII.
        assert Path(finders.find(notice)).read_bytes().isascii(), notice


def test_its_only_links_elsewhere_are_the_source_and_the_data_licences(client):
    html = page(client, 'fr')
    assert set(URL.findall(html)) == {
        settings.SOURCE_URL,
        'https://www.openstreetmap.org/copyright',
        'https://creativecommons.org/licenses/by/4.0/',
    }
    # Links only, which load nothing until followed.
    assert all('noreferrer' in link['rel'] for link in links(html) if '://' in link['href'])


def test_it_serves_the_archives_own_licences(client):
    hrefs = [link['href'] for link in links(page(client, 'fr'))]
    for name, file in (('licence_code', 'LICENSE'), ('licence_documentation', 'LICENSE-docs')):
        assert reverse(name) in hrefs
        response = client.get(reverse(name))
        assert response['Content-Type'] == 'text/plain; charset=utf-8'
        assert b''.join(response.streaming_content) == (ROOT / file).read_bytes()
        response.close()
