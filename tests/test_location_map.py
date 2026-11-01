"""The map for the stated location (SPEC.md §8, §12 M6.5): on the upload form and the code holder's
edit page, with a fresh map pass that names no upload; a place chosen on it is kept as the cell
for the precision chosen, and nothing else of it is stored or logged; nothing from elsewhere."""

import re

import pytest
from django.db import connection
from django.test import Client
from django.urls import reverse

from clips import grid
from clips.models import Clip
from maps import passes
from tests.test_confirm import code_on, full_form, start_upload, video  # noqa: F401 (fixture)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('group_key_pair')]

ENGLISH = {'Accept-Language': 'en'}
# Where a click on the map might fall, as the script fills the form: six decimals.
CLICKED = ('48.853212', '2.369187')


def style_url(html):
    return re.search(r'data-style-url="([^"]+)"', html)[1]


def pass_in(html):
    return re.fullmatch(r'/map/([^/]+)/style\.json', style_url(html))[1]


def confirmed(client, path, **changes):
    url = start_upload(client, path)
    return code_on(client.post(url, full_form(**changes), headers=ENGLISH).content.decode())


def edit_page(client, code):
    return client.post(reverse('clips:code_holder'), {'code': code}, headers=ENGLISH).content.decode()


def test_the_form_and_the_edit_page_offer_the_map_with_a_valid_pass(client, video):  # noqa: F811
    form = client.get(start_upload(client, video), headers=ENGLISH).content.decode()
    code = confirmed(client, video)
    edit = edit_page(client, code)

    for html in (form, edit):
        assert passes.is_valid(pass_in(html))
        assert 'Choose on a map' in html
    # The map's own files load only when asked for: the page names them, it does not load them.
    scripts = re.findall(r'<script src="([^"]+)"', form)
    assert '/static/clips/location_map.js' in scripts and not any('/maps/' in script for script in scripts)


def test_a_wrong_code_gets_no_map_pass(client):
    page = client.post(reverse('clips:code_holder'), {'code': 'A' * 28}, headers=ENGLISH).content.decode()

    assert 'data-style-url' not in page


def test_the_pass_names_no_upload(client, video, monkeypatch):  # noqa: F811
    # Two uploads at the same moment get the same pass.
    monkeypatch.setattr(passes.SIGNER, 'timestamp', lambda: 'same')
    first = client.get(start_upload(client, video)).content.decode()
    second = client.get(start_upload(client, video)).content.decode()

    assert style_url(first) == style_url(second)


def test_a_place_chosen_on_the_map_is_kept_as_the_cell_for_the_precision_chosen(client, video):  # noqa: F811
    confirmed(client, video, latitude=CLICKED[0], longitude=CLICKED[1], precision='street')

    clip = Clip.objects.get()
    assert clip.stated_cells == sorted(grid.children(grid.cell_for_point(float(CLICKED[0]), float(CLICKED[1]), 10), 11))


def test_nothing_of_a_click_but_its_cell_is_stored_or_logged(client, video, django_log):  # noqa: F811
    code = confirmed(
        client, video, latitude=CLICKED[0], longitude=CLICKED[1], precision='exact_spot',
        # Anything else a page might send about the map is not a field, and goes nowhere.
        zoom='16.25', map_view='2.369187,48.853212',
    )
    edited = client.post(reverse('clips:edit'), {
        **full_form(latitude='48.853377', longitude='2.369004', precision='exact_spot'), 'code': code,
    }, headers=ENGLISH)
    assert edited.status_code == 200 and Clip.objects.get().stated_cells == [grid.cell_for_point(48.853377, 2.369004, 11)]

    connection.ensure_connection()
    stored = '\n'.join(connection.connection.iterdump())  # The whole database, as SQL.
    for value in (*CLICKED, '48.853377', '2.369004', '16.25'):
        assert value not in stored and value not in django_log.getvalue()


def test_the_pages_hold_no_url_to_another_origin_but_the_licence_which_sends_no_referrer(client, video):  # noqa: F811
    form = client.get(start_upload(client, video), headers=ENGLISH).content.decode()
    edit = edit_page(client, confirmed(client, video))
    with open('clips/static/clips/location_map.js', encoding='utf-8') as script:
        source = script.read()

    for html in (form, edit):
        assert re.findall(r'https?://[^"\s<]+', html) == ['https://www.openstreetmap.org/copyright']
        assert re.search(r'<a href="https://www.openstreetmap.org/copyright"[^>]*rel="noopener noreferrer"', html)
    assert not re.findall(r'https?://', source)
