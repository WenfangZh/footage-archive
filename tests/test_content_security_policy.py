"""Every page tells the browser to load nothing from elsewhere and run no inline code, and
only lawyer pages may run WebAssembly, for libsodium.js (SPEC.md §12 M5, ADR 0006, ADR 0007).
Every page is styled by the site stylesheet alone, with no inline style (M8).

Lawyer and admin pages are found by walking the URL configuration, as in test_roles.py, so
pages added later are checked too.
"""

import pytest
from django.templatetags.static import static
from django.urls import reverse

from accounts.models import LawyerProfile
from tests.conftest import admit, log_in_with_second_factor, make_key
from tests.test_roles import ADMIN_AREA, LAWYER_AREA, area_urls, client_for  # noqa: F401 (a fixture)

pytestmark = pytest.mark.django_db

WASM = "'wasm-unsafe-eval'"


def directives(response, url):
    header = response.get('Content-Security-Policy')
    assert header, url
    policy = {}
    for part in header.split(';'):
        name, *sources = part.split()
        policy[name] = sources
    return policy


def assert_strict(policy, url):
    assert policy['default-src'] == ["'self'"], url
    assert policy['frame-ancestors'] == ["'none'"], url
    assert policy['object-src'] == ["'none'"], url
    for name, sources in policy.items():
        for source in sources:
            # Nothing inline, no eval, no other origin: only keywords and data:/blob: images.
            assert source in {"'self'", "'none'", WASM} or (name == 'img-src' and source in {'data:', 'blob:'}), (url, name, source)


def assert_styled(response, url):
    """The site stylesheet and no inline style; the archive's name and the language switch in
    the header, and the licences page in the footer (M8)."""
    # Only pages: redirects, refusals of a method and JSON have nothing to style.
    if response.status_code != 200 or not response['Content-Type'].startswith('text/html'):
        return
    page = response.content.decode()
    assert f'<link rel="stylesheet" href="{static("core/archive.css")}">' in page, url
    assert '<style' not in page and ' style=' not in page, url
    header, footer = page.split('<main>')[0], page.split('</main>')[1]
    assert f'class="site-name" href="{reverse("home")}"' in header, url
    assert f'action="{reverse("set_language")}"' in header, url
    assert f'href="{reverse("licences")}"' in footer, url


def test_every_lawyer_page_may_run_webassembly(client_for):
    client = client_for('lawyer')
    for url in area_urls(LAWYER_AREA):
        response = client.get(url)
        assert_styled(response, url)
        policy = directives(response, url)
        assert_strict(policy, url)
        assert policy['script-src'] == ["'self'", WASM], url


@pytest.mark.parametrize('kind', ['admin', None])
def test_no_other_page_may_run_webassembly(client_for, client, kind):
    client = client_for(kind) if kind else client
    urls = area_urls(ADMIN_AREA) + [
        reverse('home'), reverse('clips:upload'), reverse('clips:code_holder'), reverse('accounts:login'),
    ]
    for url in urls:
        response = client.get(url)
        assert_styled(response, url)
        policy = directives(response, url)
        assert_strict(policy, url)
        assert WASM not in policy.get('script-src', []), url


def test_the_pages_where_a_key_is_set_up_unlocked_or_replaced_may_run_webassembly(client, django_user_model):
    user = django_user_model.objects.create_user('applicant@example.org', 'a long test passphrase')
    make_key(LawyerProfile.objects.create(user=user, bar='Paris'))
    log_in_with_second_factor(client, user)
    for name in ['accounts:key_setup', 'accounts:key', 'accounts:key_recover']:
        url = reverse(name)
        response = client.get(url)
        assert response.status_code == 200, url
        policy = directives(response, url)
        assert_strict(policy, url)
        assert policy['script-src'] == ["'self'", WASM], url
    # A lawyer's only: replacing a key that may have been seen.
    profile = user.lawyer_profile
    admit(profile)
    url = reverse('accounts:key_exposure')
    response = client.get(url)
    assert response.status_code == 200 and directives(response, url)['script-src'] == ["'self'", WASM]


def test_a_lawyer_url_seen_by_someone_else_gets_the_policy_too(client):
    # Logged out: a redirect to the login page, which carries the policy like any response.
    url = reverse('lawyer:home')
    assert_strict(directives(client.get(url), url), url)
