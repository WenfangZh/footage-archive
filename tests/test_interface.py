"""The interface (SPEC.md §12 M8): the stylesheet's colours are readable, and each role's
header links reach its pages."""

import re

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse

from tests.conftest import log_in_with_second_factor, make_lawyer
from tests.test_confirm import start_upload, video  # noqa: F401 (a fixture)
from tests.test_roles import client_for  # noqa: F401 (a fixture)

pytestmark = pytest.mark.django_db

User = get_user_model()

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


def role_links(response):
    """The header's links to any role's pages, in order."""
    every = {reverse(name) for names in HEADER_PAGES.values() for name in names}
    header = response.content.decode().split('<main>')[0]
    return [href for href in re.findall(r'<a href="([^"]+)"', header) if href in every]


@pytest.mark.parametrize('kind', ['lawyer', 'admin'])
def test_the_header_links_each_role_to_its_pages_and_no_others(client, kind):
    user = User.objects.create_superuser('admin@example.org') if kind == 'admin' else make_lawyer(key=True).user
    # The lawyer has a personal key, without which the key page sends them to set one up.
    client = log_in_with_second_factor(client, user)
    urls = [reverse(name) for name in HEADER_PAGES[kind]]
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, url
        assert role_links(response) == urls, url


@pytest.mark.parametrize('kind', ['applicant', 'suspended', 'no second factor', 'logged out'])
def test_nobody_else_gets_links_to_role_pages(client_for, client, kind):
    if kind == 'logged out':
        response = client.get(reverse('home'))
    else:
        if kind == 'no second factor':
            client.force_login(make_lawyer().user)  # A password, but no code yet.
        else:
            client = client_for(kind)
        response = client.get(reverse('accounts:home'), follow=True)
    # A suspended lawyer is refused with a page that has the header too.
    assert response.status_code in (200, 403)
    assert role_links(response) == []
    assert (reverse('accounts:logout') in response.content.decode()) == (kind != 'logged out')


@pytest.mark.parametrize('language, descriptions', [
    ('fr', ["Pas besoin de compte", "Modifier les informations de votre clip", "Avec votre mot de passe"]),
    ('en', ["No account needed", "Change your clip's details", "With your password"]),
])
def test_the_front_page_offers_three_paths_with_what_each_leads_to(client, language, descriptions):
    page = client.get(reverse('home'), HTTP_ACCEPT_LANGUAGE=language).content.decode()
    paths = re.findall(r'<li><a href="([^"]+)">', page)
    assert paths == [reverse('clips:upload'), reverse('clips:code_holder'), reverse('accounts:login')]
    for description in descriptions:
        assert description in page


def test_each_upload_step_marks_its_own_step(client, video):
    def marked(page):
        return re.findall(r'<li aria-current="step">(\d)', page)

    assert marked(client.get(reverse('clips:upload')).content.decode()) == ['1']
    assert marked(client.get(start_upload(client, video)).content.decode()) == ['2']
