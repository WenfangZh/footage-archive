"""The interface (SPEC.md §12 M8): the stylesheet's colours are readable, and each role's
header links reach its pages."""

import re

import pytest
from django.conf import settings
from django.urls import reverse

from tests.conftest import log_in_with_second_factor, make_lawyer
from tests.test_roles import client_for  # noqa: F401 (a fixture)

pytestmark = pytest.mark.django_db

STYLESHEET = settings.BASE_DIR / 'core' / 'static' / 'core' / 'archive.css'

# WCAG 2.2 AA: 4.5:1 for text (1.4.3), 3:1 for the borders of controls (1.4.11).
TEXT = ['ink', 'muted', 'accent', 'danger']
CONTROL_BORDERS = ['ink', 'muted', 'danger']
BACKGROUNDS = ['paper', 'tint']


def colours(block):
    return dict(re.findall(r'--(\w+):\s*(#[0-9a-f]{6});', block))


def themes():
    css = STYLESHEET.read_text()
    light = colours(re.search(r'^:root \{(.*?)\}', css, re.S | re.M).group(1))
    dark = colours(re.search(r'prefers-color-scheme: dark\) \{\s*:root \{(.*?)\}', css, re.S).group(1))
    return {'light': light, 'dark': {**light, **dark}}


def luminance(colour):
    channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    high, low = sorted([luminance(a), luminance(b)], reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_text_and_controls_meet_aa_contrast(theme):
    palette = themes()[theme]
    for background in BACKGROUNDS:
        for name in TEXT:
            assert contrast(palette[name], palette[background]) >= 4.5, (theme, name, background)
        for name in CONTROL_BORDERS:
            assert contrast(palette[name], palette[background]) >= 3, (theme, name, background)


HEADER_PAGES = {
    'lawyer': ['lawyer:home', 'lawyer:messages', 'accounts:key', 'lawyer:settings'],
    'admin': ['manage:home', 'manage:invite', 'manage:case_requests', 'manage:disputed_tags', 'manage:withdrawn_clips'],
}


def header(response):
    return response.content.decode().split('<main>')[0]


@pytest.mark.parametrize('kind', ['lawyer', 'admin'])
def test_the_header_links_each_role_to_its_pages(client, admin_client, kind):
    # A lawyer with a personal key, without which the key page sends them to set one up.
    client = admin_client if kind == 'admin' else log_in_with_second_factor(client, make_lawyer(key=True).user)
    urls = [reverse(name) for name in HEADER_PAGES[kind]]
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, url
        links = re.findall(r'<a href="([^"]+)"( aria-current="page")?>', header(response))
        assert [href for href, _ in links if href in urls] == urls, url
        assert [href for href, current in links if current] == [url], url


@pytest.mark.parametrize('kind', ['applicant', 'suspended'])
def test_people_without_a_role_page_get_no_links_but_logging_out(client_for, kind):
    page = header(client_for(kind).get(reverse('accounts:home'), follow=True))
    assert reverse('accounts:logout') in page
    for names in HEADER_PAGES.values():
        for name in names:
            assert f'href="{reverse(name)}"' not in page
