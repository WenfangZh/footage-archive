"""The theme switch (SPEC.md §12 M8.5): light or dark on every page, back to the system's
choice, without a script; the cookie holds only the choice."""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def choose(client, theme, back_to='/licences/'):
    return client.post(reverse('set_theme'), {'theme': theme, 'next': back_to})


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_a_chosen_theme_is_kept_in_a_cookie_holding_only_it(client, theme):
    response = choose(client, theme)
    assert response.status_code == 302 and response['Location'] == '/licences/'
    cookie = response.cookies['theme']
    assert cookie.value == theme
    assert cookie['samesite'] == 'Lax' and cookie['max-age'] == 365 * 24 * 3600
    page = client.get(reverse('home')).content.decode()
    assert f'<html lang="fr" data-theme="{theme}">' in page
    assert f'value="{theme}" aria-current="true" disabled' in page


def test_the_systems_choice_deletes_the_cookie(client):
    choose(client, 'dark')
    response = choose(client, 'system')
    assert response.cookies['theme'].value == '' and response.cookies['theme']['max-age'] == 0
    assert 'data-theme' not in client.get(reverse('home')).content.decode()


def test_an_unknown_cookie_value_counts_as_the_systems_choice(client):
    client.cookies['theme'] = 'purple"><script>'
    page = client.get(reverse('home')).content.decode()
    assert 'data-theme' not in page and 'purple' not in page
    assert 'value="system" aria-current="true" disabled' in page


def test_the_switch_returns_only_to_a_page_of_the_archive(client):
    assert choose(client, 'dark', back_to='https://elsewhere.example/')['Location'] == '/'
