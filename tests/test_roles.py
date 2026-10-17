"""Each role's permissions (SPEC.md §5, §12 M3): nobody but a lawyer sees a lawyer page,
nobody but the admin sees an admin page.

The pages are found by walking the URL configuration, so every page added under the
lawyer's or the admin's prefix later is checked here too.
"""

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.urls import URLResolver, get_resolver, reverse
from django.urls.resolvers import RoutePattern

from accounts.models import LawyerProfile
from accounts.roles import Role, role
from tests.conftest import log_in_with_second_factor

pytestmark = pytest.mark.django_db

User = get_user_model()
Status = LawyerProfile.Status

LAWYER_AREA = 'lawyer/'
ADMIN_AREA = 'manage/'

# Made-up values for the parts of a URL: access is checked before they are looked at.
DUMMY_VALUES = {
    'IntConverter': 1, 'UUIDConverter': uuid.UUID(int=1),
    'StringConverter': 'x', 'SlugConverter': 'x', 'PathConverter': 'x',
}

# Every kind of account but the lawyer and the admin. Suspended, revoked and declined
# accounts are left active here, to show that the status alone shuts them out; their
# sessions end anyway once suspension, revocation or declining deactivate them.
SHUT_OUT = ['applicant', 'declined', 'suspended', 'revoked', 'neither']


def area_urls(prefix):
    urls = []

    def walk(patterns, route, namespace):
        for pattern in patterns:
            full_route = route + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, full_route, ':'.join(filter(None, [namespace, pattern.namespace])))
            else:
                # A regular expression's route could not be matched against the prefixes.
                assert isinstance(pattern.pattern, RoutePattern), f'Use path(), not re_path(): {full_route}'
            if not isinstance(pattern, URLResolver) and full_route.startswith(prefix):
                assert pattern.name, f'Every page under {prefix} needs a name, for this test.'
                kwargs = {
                    name: DUMMY_VALUES[type(converter).__name__]
                    for name, converter in pattern.pattern.converters.items()
                }
                urls.append(reverse(':'.join(filter(None, [namespace, pattern.name])), kwargs=kwargs))

    walk(get_resolver().url_patterns, '', '')
    return urls


def make_account(kind):
    if kind == 'admin':
        return User.objects.create_superuser('admin@example.org')
    user = User.objects.create_user(f'{kind}@example.org')
    if kind == 'neither':
        return user
    profile = LawyerProfile.objects.create(user=user, bar='Paris')
    if kind == 'declined':
        profile.change_status(Status.DECLINED)
    if kind in ('lawyer', 'suspended', 'revoked'):
        profile.verified_via = 'The bar directory'
        profile.vouched_by = 'A defence organisation'
        profile.save()
        profile.change_status(Status.LAWYER)
    if kind in ('suspended', 'revoked'):
        profile.change_status(Status(kind))
    return user


@pytest.fixture
def client_for(client):
    def log_in(kind):
        return log_in_with_second_factor(client, make_account(kind))
    return log_in


def test_both_areas_have_pages():
    assert area_urls(LAWYER_AREA)
    assert area_urls(ADMIN_AREA)


@pytest.mark.parametrize('area', [LAWYER_AREA, ADMIN_AREA])
@pytest.mark.parametrize('method', ['get', 'post'])
def test_nobody_logged_out_sees_either_area(client, area, method):
    for url in area_urls(area):
        response = getattr(client, method)(url)
        assert response.status_code == 302, url
        assert response['Location'].startswith(reverse('accounts:login')), url


@pytest.mark.parametrize('kind', SHUT_OUT + ['admin'])
@pytest.mark.parametrize('method', ['get', 'post'])
def test_nobody_but_a_lawyer_sees_a_lawyer_page(client_for, kind, method):
    client = client_for(kind)
    for url in area_urls(LAWYER_AREA):
        response = getattr(client, method)(url)
        assert response.status_code == 403, url
        assert [template.name for template in response.templates][0] == 'accounts/no_access.html'


@pytest.mark.parametrize('kind', SHUT_OUT + ['lawyer'])
@pytest.mark.parametrize('method', ['get', 'post'])
def test_nobody_but_the_admin_sees_an_admin_page(client_for, kind, method):
    client = client_for(kind)
    for url in area_urls(ADMIN_AREA):
        response = getattr(client, method)(url)
        assert response.status_code == 403, url
        assert [template.name for template in response.templates][0] == 'accounts/no_access.html'


def assert_never_cached(response, url):
    assert 'no-store' in response.get('Cache-Control', ''), url


@pytest.mark.parametrize('kind', [None, 'applicant', 'lawyer', 'admin'])
@pytest.mark.parametrize('area', [LAWYER_AREA, ADMIN_AREA])
def test_no_response_from_either_area_is_cached(client_for, client, kind, area):
    client = client_for(kind) if kind else client
    for url in area_urls(area):
        assert_never_cached(client.get(url), url)


@pytest.mark.parametrize('kind, area', [('lawyer', LAWYER_AREA), ('admin', ADMIN_AREA)])
@pytest.mark.parametrize('method', ['get', 'post'])
def test_no_page_opens_without_a_second_factor(client, kind, area, method):
    client.force_login(make_account(kind))  # A password, but no code yet.
    for url in area_urls(area):
        response = getattr(client, method)(url)
        assert response.status_code == 302, url
        assert response['Location'].startswith(reverse('accounts:two_factor_setup')), url


def test_a_lawyer_sees_the_lawyer_pages(client_for):
    client = client_for('lawyer')
    assert client.get(reverse('lawyer:home')).status_code == 200


def test_the_admin_sees_the_admin_pages(client_for):
    client = client_for('admin')
    assert client.get(reverse('manage:home')).status_code == 200


def test_an_account_made_inactive_is_logged_out_at_once(client_for):
    client = client_for('lawyer')
    User.objects.filter(email='lawyer@example.org').update(is_active=False)
    response = client.get(reverse('lawyer:home'))
    assert response.status_code == 302
    assert response['Location'].startswith(reverse('accounts:login'))


@pytest.mark.parametrize('kind, expected', [
    ('admin', Role.ADMIN),
    ('lawyer', Role.LAWYER),
    ('applicant', Role.APPLICANT),
    ('declined', Role.NO_ACCESS),
    ('suspended', Role.NO_ACCESS),
    ('revoked', Role.NO_ACCESS),
    ('neither', Role.NO_ACCESS),
])
def test_each_account_has_one_role(kind, expected):
    assert role(make_account(kind)) == expected


def test_a_status_change_applies_to_the_next_request(client_for):
    client = client_for('lawyer')
    assert client.get(reverse('lawyer:home')).status_code == 200
    LawyerProfile.objects.get(user__email='lawyer@example.org').change_status(Status.SUSPENDED)
    assert client.get(reverse('lawyer:home')).status_code == 403
