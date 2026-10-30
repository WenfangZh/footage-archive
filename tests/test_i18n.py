from pathlib import Path

import pytest
from django.urls import reverse

BASE_DIR = Path(__file__).resolve().parent.parent
LOCALE_DIR = BASE_DIR / 'locale'

FRENCH_NOTICE = "Version d'apprentissage : non déployée, à ne pas utiliser pour de vraies vidéos."
ENGLISH_NOTICE = 'Learning build: not deployed, not for real footage.'


def test_compiled_translations_are_up_to_date():
    for po_file in LOCALE_DIR.glob('*/LC_MESSAGES/*.po'):
        mo_file = po_file.with_suffix('.mo')
        assert mo_file.exists() and mo_file.stat().st_mtime >= po_file.stat().st_mtime, (
            f'{mo_file.relative_to(BASE_DIR)} is missing or older than its .po file: '
            'run `python manage.py compilemessages`'
        )


def home_text(client, **headers):
    response = client.get(reverse('home'), headers=headers)
    assert response.status_code == 200
    return response.content.decode()


def test_home_is_french_by_default(client):
    html = home_text(client)
    assert '<html lang="fr">' in html
    assert FRENCH_NOTICE in html


@pytest.mark.parametrize('accept_language', ['de', 'es-ES,es;q=0.9'])
def test_unsupported_browser_language_falls_back_to_french(client, accept_language):
    assert FRENCH_NOTICE in home_text(client, accept_language=accept_language)


def test_browser_preferring_english_gets_english(client):
    html = home_text(client, accept_language='en-GB,en;q=0.9,fr;q=0.5')
    assert '<html lang="en">' in html
    assert ENGLISH_NOTICE in html


def test_language_switch_is_visible_in_both_languages(client):
    html = home_text(client)
    assert reverse('set_language') in html
    assert 'value="fr"' in html and 'value="en"' in html
    assert 'Français' in html and 'English' in html


def test_switching_to_english_and_back(client):
    response = client.post(reverse('set_language'), {'language': 'en', 'next': '/'})
    assert response.status_code == 302
    assert ENGLISH_NOTICE in home_text(client)

    client.post(reverse('set_language'), {'language': 'fr', 'next': '/'})
    assert FRENCH_NOTICE in home_text(client)


def test_switch_beats_browser_preference(client):
    client.post(reverse('set_language'), {'language': 'fr', 'next': '/'})
    assert FRENCH_NOTICE in home_text(client, accept_language='en')


def test_catalogues_carry_no_creation_date():
    # The project's makemessages drops it: a time stamped by each run made any two branches
    # that added translations conflict.
    for po_file in LOCALE_DIR.glob('*/LC_MESSAGES/*.po'):
        assert 'POT-Creation-Date' not in po_file.read_text(encoding='utf-8')
