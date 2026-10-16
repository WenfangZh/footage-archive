import re
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import translation

from clips.tags import DEFAULT_TAGS
from tests.media import H264

pytestmark = pytest.mark.django_db

BASE_DIR = Path(__file__).resolve().parent.parent
FRENCH = {}  # French is the default: no header needed.
ENGLISH = {'accept_language': 'en'}


@pytest.fixture
def video(make_media):
    return make_media(
        'phone.mp4', *H264, '-an',
        '-metadata', 'creation_time=2026-05-01T12:03:22Z', '-metadata', 'location=+48.8532+002.3692/',
    )


def start_upload(client, video):
    with open(video, 'rb') as file:
        return client.post(reverse('clips:upload'), {'file': file})['Location']


def page(client, url, headers, data=None):
    response = client.post(url, data, headers=headers) if data is not None else client.get(url, headers=headers)
    return response.content.decode()


@pytest.mark.parametrize('headers, expected', [
    (FRENCH, ['<html lang="fr">', 'Envoyer une vidéo', 'Taille maximale : 2 Go.', 'Envoyer</button>']),
    (ENGLISH, ['<html lang="en">', 'Upload a video', 'Maximum size: 2 GB.', 'Send</button>']),
])
def test_the_upload_form_in_both_languages(client, headers, expected):
    html = page(client, reverse('clips:upload'), headers)
    for text in expected:
        assert text in html


@pytest.mark.parametrize('headers, expected', [
    (FRENCH, ['Quand la vidéo a-t-elle été filmée ?', 'Endroit exact', 'Quartier', 'Interpellation', 'Nasse',
              'Avec quelle précision pouvons-nous garder la position GPS trouvée dans la vidéo ?',
              'Laisser un moyen de contact (facultatif)', "Confirmer et obtenir mon code d'upload"]),
    (ENGLISH, ['When was it filmed?', 'Exact spot', 'Neighbourhood', 'Arrest', 'Kettling',
               'How precisely may we keep the GPS position found in the video?',
               'Leave contact details (optional)', 'Confirm and get my upload code']),
])
def test_the_confirm_form_in_both_languages(client, video, headers, expected):
    html = page(client, start_upload(client, video), headers)
    for text in expected:
        assert text in html


@pytest.mark.parametrize('headers, expected', [
    (FRENCH, ["Votre code d'upload", 'Fiche de code', 'value="01/05/2026 – Interpellation, Nasse"',
              'Toute personne qui trouve cette fiche peut lire les messages du clip et le supprimer.']),
    (ENGLISH, ['Your upload code', 'Code card', 'value="05/01/2026 – Arrest, Kettling (crowd encircled and held by police)"',
               "Anyone who finds this card can read the clip's messages and delete it."]),
])
def test_the_code_card_in_both_languages(client, video, headers, expected):
    url = start_upload(client, video)
    html = page(client, url, headers, {'stated_time': '2026-05-01T14:03', 'tags': ['arrest', 'kettling'],
                                      'metadata_precision': '11'})
    for text in expected:
        assert text in html


@pytest.mark.parametrize('headers, expected', [
    (FRENCH, ["Revenir avec votre code d'upload", 'Ouvrir mon clip']),
    (ENGLISH, ['Come back with your upload code', 'Open my clip']),
])
def test_the_code_entry_in_both_languages(client, headers, expected):
    html = page(client, reverse('clips:code_holder'), headers)
    for text in expected:
        assert text in html


@pytest.mark.parametrize('headers, expected', [
    (FRENCH, ['Votre clip', 'En préparation', 'Enregistrer les modifications', 'Supprimer ce clip']),
    (ENGLISH, ['Your clip', 'Processing', 'Save changes', 'Delete this clip']),
])
def test_the_clip_page_in_both_languages(client, video, headers, expected):
    card = page(client, start_upload(client, video), ENGLISH, {'metadata_precision': '11'})
    code = re.search(r'<code>([0-9A-Z-]+)</code>', card)[1]
    html = page(client, reverse('clips:code_holder'), headers, {'code': code})
    for text in expected:
        assert text in html


def spec_tag_names():
    """The default tags table of SPEC.md §8: {key: (English, French)}."""
    rows = re.findall(r'^\| `(\w+)` \| ([^|]+) \| ([^|]+) \|$', (BASE_DIR / 'SPEC.md').read_text(), re.M)
    return {key: (english.strip(), french.strip()) for key, english, french in rows}


def test_default_tags_match_the_spec_in_both_languages():
    names = spec_tag_names()
    assert set(names) == set(DEFAULT_TAGS)
    for key, (english, french) in names.items():
        with translation.override('en'):
            assert str(DEFAULT_TAGS[key]) == english
        with translation.override('fr'):
            assert str(DEFAULT_TAGS[key]) == french


def test_every_string_has_a_french_translation():
    po = (BASE_DIR / 'locale' / 'fr' / 'LC_MESSAGES' / 'django.po').read_text()
    entries = po.split('\n\n')[1:]  # The first entry is the header.
    untranslated = [entry for entry in entries if re.search(r'^msgstr ""\n?$', entry, re.M)
                    and not re.search(r'^msgstr ""\n"', entry, re.M)]
    fuzzy = [entry for entry in entries if '#, fuzzy' in entry]
    assert untranslated == [] and fuzzy == []
