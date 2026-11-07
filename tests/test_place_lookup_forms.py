"""The place lookup without JavaScript (SPEC.md §12 M9, ADR 0011): on the upload form, the
code holder's page and a lawyer's new case, "Look up" is the first button; it lists streets (or
addresses, for a case) and keeps the rest of the form, and a chosen match fills the typed
coordinates; nothing confirms an upload, saves an edit or creates a case. Without the data, the
forms say so and work as before."""

import logging
import re

import pytest
from django.test import Client
from django.urls import reverse

from cases.models import Case
from clips.models import Clip, Edit
from tests.conftest import database_dump, log_in_with_second_factor, make_lawyer
from tests.test_code_holder import EDIT_URL, form_values, open_clip, uploaded  # noqa: F401 (fixture)
from tests.test_confirm import full_form, start_upload, video  # noqa: F401 (fixture)
from tests.test_place_lookup import index  # noqa: F401 (fixture)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('group_key_pair')]

ENGLISH = {'Accept-Language': 'en'}
STREET = 'Boulevard Saint-Germain, 75005 Paris 5e Arrondissement'
# Its point in the test index: the address nearest its centre.
POINT = ('48.85', '2.35')
NO_COORDINATES = {'latitude': None, 'longitude': None, 'precision': None}
NEW_CASE_URL = reverse('lawyer:new_case')
ADDRESS = '2 Place de la République, 75011 Paris 11e Arrondissement'
ADDRESS_POINT = ('48.867', '2.364')


def main_form(html, action):
    form = html[html.index(f'<form method="post"{action}'):]
    return form[:form.index('</form>')]


def submit_buttons(form):
    return [tag for tag in re.findall(r'<button[^>]*>', form) if 'type="button"' not in tag]


@pytest.fixture
def lawyer_client():
    return log_in_with_second_factor(Client(), make_lawyer().user)


def edit_values(client, code, **changes):
    values = form_values(open_clip(client, code).content.decode())
    values.update(changes)
    return {key: value for key, value in values.items() if value is not None}


def test_look_up_is_the_first_button_of_each_form(index, client, video, uploaded, lawyer_client):  # noqa: F811
    _, code = uploaded
    upload_form = main_form(client.get(start_upload(client, video)).content.decode(), '')
    edit_form = main_form(open_clip(client, code).content.decode(), f' action="{EDIT_URL}"')
    case_form = main_form(lawyer_client.get(NEW_CASE_URL).content.decode(), f' action="{NEW_CASE_URL}"')
    for form in (upload_form, edit_form, case_form):
        # Enter in a field presses the first: it runs a lookup, never the form's main action.
        assert 'name="lookup"' in submit_buttons(form)[0]


def test_on_the_upload_form_a_lookup_and_a_choice_keep_the_form_and_confirm_nothing(index, client, video):  # noqa: F811
    url = start_upload(client, video)
    looked_up = client.post(url, full_form(**NO_COORDINATES, place='bd st germain', lookup='yes'), headers=ENGLISH)
    html = looked_up.content.decode()
    assert looked_up.status_code == 200 and STREET in html
    assert 'Filmed from across the street.' in html and 'value="Pont Neuf, Barricade"' in html
    assert 'value="bd st germain"' in html
    # Nothing is checked yet: the empty coordinates are not an error.
    assert 'errorlist' not in html

    chosen = client.post(url, full_form(**NO_COORDINATES, place='bd st germain', choose=' '.join(POINT)), headers=ENGLISH)
    html = chosen.content.decode()
    assert f'value="{POINT[0]}"' in html and f'value="{POINT[1]}"' in html and 'value="bd st germain"' in html
    assert re.search(r'value="street"[^>]*checked', html)
    assert 'one point of the street' in html and '<details id="typed-place" open>' in html
    assert 'Filmed from across the street.' in html
    # No upload code is issued until the form is confirmed.
    assert not Clip.objects.exists() and '<code>' not in html


def test_on_the_code_holders_page_a_lookup_and_a_choice_keep_the_form_and_save_nothing(index, client, uploaded):  # noqa: F811
    clip, code = uploaded
    before = (Clip.objects.get().stated_cells, Edit.objects.count())
    looked_up = client.post(EDIT_URL, edit_values(client, code, context='Changed.', place='bd st germain', lookup='yes'), headers=ENGLISH)
    html = looked_up.content.decode()
    assert looked_up.status_code == 200 and STREET in html and 'Changed.' in html

    chosen = client.post(EDIT_URL, edit_values(client, code, context='Changed.', choose=' '.join(POINT)), headers=ENGLISH)
    html = chosen.content.decode()
    assert f'value="{POINT[0]}"' in html and re.search(r'value="street"[^>]*checked', html) and 'Changed.' in html
    assert (Clip.objects.get().stated_cells, Edit.objects.count()) == before
    assert 'Your changes are saved.' not in html


def test_on_a_new_case_a_lookup_finds_addresses_and_a_choice_fills_the_place_but_creates_no_case(index, lawyer_client):  # noqa: F811
    form = {'label': 'Test case', 'radius_metres': '150'}
    looked_up = lawyer_client.post(NEW_CASE_URL, {**form, 'place': '2 place republique', 'lookup': 'yes'}, headers=ENGLISH)
    html = looked_up.content.decode()
    assert looked_up.status_code == 200 and ADDRESS in html
    assert 'value="Test case"' in html and 'value="150"' in html and 'errorlist' not in html

    chosen = lawyer_client.post(NEW_CASE_URL, {**form, 'place': '2 place republique', 'choose': ' '.join(ADDRESS_POINT)}, headers=ENGLISH)
    html = chosen.content.decode()
    # The point is shown, for the lawyer to set the radius around it; a refused form forgets it.
    assert f'value="{ADDRESS_POINT[0]}"' in html and f'value="{ADDRESS_POINT[1]}"' in html
    assert 'Set the radius around it.' in html and '<details id="typed-place" open>' in html
    assert not Case.objects.exists()
    assert {'place', 'choose'} <= set(chosen.wsgi_request.sensitive_post_parameters)


def test_without_the_data_the_forms_say_so_and_work_as_before(settings, tmp_path, client, video, uploaded, lawyer_client):  # noqa: F811
    settings.PLACES_DIR = tmp_path
    _, code = uploaded
    url = start_upload(client, video)
    for html in (client.get(url, headers=ENGLISH).content.decode(), open_clip(client, code).content.decode()):
        assert 'Looking up a street is unavailable for now.' in html and 'name="place"' not in html
    confirmed = client.post(url, full_form(), headers=ENGLISH).content.decode()
    assert '<code>' in confirmed
    case_page = lawyer_client.get(NEW_CASE_URL, headers=ENGLISH).content.decode()
    assert 'Looking up an address is unavailable for now.' in case_page and 'name="place"' not in case_page


def test_lookups_by_button_are_rate_limited_and_never_kept_or_logged(index, settings, client, video, django_log):  # noqa: F811
    settings.RATE_LIMIT_PLACE_LOOKUPS = 1
    url = start_upload(client, video)
    before = database_dump()
    first = client.post(url, {'place': 'rue de la paix', 'lookup': 'yes'}, headers=ENGLISH)
    assert 'Rue de la Paix, 75002' in first.content.decode()
    refused = client.post(url, {'place': 'rue de la paix', 'lookup': 'yes'}, headers=ENGLISH)
    assert refused.status_code == 429 and refused['Retry-After'] == '3600' and 'Too many lookups' in refused.content.decode()
    logging.getLogger('django.server').info('"%s" %s %s', f'POST {url} HTTP/1.1', 200, 128)
    assert 'paix' not in django_log.getvalue() and database_dump() == before
    # Error reports hide the query and the match chosen (Django's SafeExceptionReporterFilter reads this).
    assert {'place', 'choose'} <= set(first.wsgi_request.sensitive_post_parameters)
