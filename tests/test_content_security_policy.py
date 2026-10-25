"""Every page tells the browser to load nothing from elsewhere and run no inline code, and
only lawyer pages may run WebAssembly, for libsodium.js (SPEC.md §12 M5, ADR 0006, ADR 0007).

Lawyer and admin pages are found by walking the URL configuration, as in test_roles.py, so
pages added later are checked too.
"""

import pytest
from django.urls import reverse

from accounts.models import LawyerProfile
from tests.conftest import log_in_with_second_factor, make_key
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


def test_every_lawyer_page_may_run_webassembly(client_for):
    client = client_for('lawyer')
    for url in area_urls(LAWYER_AREA):
        policy = directives(client.get(url), url)
        assert_strict(policy, url)
        assert policy['script-src'] == ["'self'", WASM], url


@pytest.mark.parametrize('kind', ['admin', None])
def test_no_other_page_may_run_webassembly(client_for, client, kind):
    client = client_for(kind) if kind else client
    urls = area_urls(ADMIN_AREA) + [
        reverse('home'), reverse('clips:upload'), reverse('clips:code_holder'), reverse('accounts:login'),
    ]
    for url in urls:
        policy = directives(client.get(url), url)
        assert_strict(policy, url)
        assert WASM not in policy.get('script-src', []), url


def test_the_pages_where_an_applicant_sets_up_or_unlocks_a_key_may_run_webassembly(client, django_user_model):
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


def test_a_lawyer_url_seen_by_someone_else_gets_the_policy_too(client):
    # Logged out: a redirect to the login page, which carries the policy like any response.
    url = reverse('lawyer:home')
    assert_strict(directives(client.get(url), url), url)
