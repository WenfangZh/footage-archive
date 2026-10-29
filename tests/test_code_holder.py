import datetime
import re

import pytest
from django.db import connection
from django.urls import reverse

from clips import grid, vault
from clips.upload_codes import normalise
from clips.models import Clip, ClipTag, Edit, Original, Precision, VaultText
from clips.time_blocks import four_hour_block
from tests.media import H264

# Texts are sealed to the group keys in use when they are written: one exists in each test here.
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('group_key_pair')]

UTC = datetime.UTC
ENGLISH = {'accept_language': 'en'}
ENTRY_URL = reverse('clips:code_holder')
EDIT_URL = reverse('clips:edit')
DELETE_URL = reverse('clips:delete')
# Fake positions of public places.
FILE_GPS = (48.8532, 2.3692)  # Place de la Bastille
STATED = (48.8675, 2.3638)  # Place de la République
ELSEWHERE = (48.8606, 2.3376)  # Louvre


@pytest.fixture
def uploaded(client, make_media):
    """Upload a synthetic video through both steps; returns (clip, code as shown)."""
    video = make_media(
        'phone.mp4', *H264, '-c:a', 'aac',
        '-metadata', 'creation_time=2026-05-01T12:03:22Z', '-metadata', 'location=+48.8532+002.3692/',
    )
    with open(video, 'rb') as file:
        confirm_url = client.post(reverse('clips:upload'), {'file': file})['Location']
    response = client.post(confirm_url, {
        'stated_time': '2026-05-01T14:05',
        'latitude': str(STATED[0]), 'longitude': str(STATED[1]), 'precision': Precision.EXACT_SPOT,
        'metadata_precision': '11',
        'tags': ['arrest'], 'custom_tags': 'Barricade',
        'context': 'First context.', 'contact_details': '@first-test-handle',
    })
    code = re.search(r'<code>([0-9A-Z-]+)</code>', response.content.decode())[1]
    return Clip.objects.get(), code


def open_clip(client, code):
    return client.post(ENTRY_URL, {'code': code}, headers=ENGLISH)


def form_values(html):
    """What the edit form would send back unchanged, read from its HTML."""
    form = html[html.index(f'action="{EDIT_URL}"'):]
    form = form[:form.index('</form>')]
    values = {}
    for tag in re.findall(r'<input[^>]*>', form):
        name = re.search(r'name="([^"]+)"', tag)
        value = re.search(r'value="([^"]*)"', tag)
        if not name or name[1] == 'csrfmiddlewaretoken':
            continue
        if 'type="checkbox"' in tag or 'type="radio"' in tag:
            if 'checked' in tag:
                values.setdefault(name[1], []).append(value[1])
        else:
            values[name[1]] = value[1] if value else ''
    for name, text in re.findall(r'<textarea name="([^"]+)"[^>]*>\n?(.*?)</textarea>', form, re.S):
        values[name] = text
    return values


def edit(client, code, **changes):
    values = form_values(open_clip(client, code).content.decode())
    values.update(changes)
    return client.post(EDIT_URL, {key: value for key, value in values.items() if value is not None}, headers=ENGLISH)


def test_the_entry_page_sends_the_code_by_post(client):
    html = client.get(ENTRY_URL, headers=ENGLISH).content.decode()
    assert 'method="post"' in html and 'name="code"' in html
    assert 'autocomplete="off"' in html


def test_the_right_code_opens_the_clip_however_it_is_typed(client, uploaded):
    clip, code = uploaded
    response = open_clip(client, code.lower().replace('-', ' '))
    html = response.content.decode()
    assert response.status_code == 200
    assert 'no-store' in response['Cache-Control']
    assert 'First context.' in html and '@first-test-handle' in html
    assert 'value="Barricade"' in html


@pytest.mark.parametrize('typed', ['7K3Q-M9XA-2B4C-D6EF-8GHJ-0KMN-1PQR', 'not a code', ''])
def test_a_wrong_code_opens_nothing(client, uploaded, typed):
    response = open_clip(client, typed)
    assert response.status_code == 404
    assert 'No clip has this code.' in response.content.decode()
    assert 'First context.' not in response.content.decode()


def test_the_code_holder_never_sees_lawyer_tags(client, uploaded):
    clip, code = uploaded
    ClipTag.objects.create(clip=clip, custom_name='lawyer-only note', source=ClipTag.Source.LAWYER)
    assert 'lawyer-only note' not in open_clip(client, code).content.decode()


def test_saving_without_changes_records_no_edit(client, uploaded):
    clip, code = uploaded
    response = edit(client, code)
    assert response.status_code == 200
    assert not Edit.objects.exists()
    clip_after = Clip.objects.get()
    assert (clip_after.stated_time, clip_after.stated_cell) == (clip.stated_time, clip.stated_cell)


def test_an_edit_records_the_field_and_its_block_only(client, uploaded):
    clip, code = uploaded
    response = edit(client, code, stated_time='2026-05-01T14:30')
    assert 'Your changes are saved.' in response.content.decode()
    clip.refresh_from_db()
    assert clip.stated_time == datetime.datetime(2026, 5, 1, 12, 30, tzinfo=UTC)
    assert clip.as_uploaded_stated_time == datetime.datetime(2026, 5, 1, 12, 5, tzinfo=UTC)
    [record] = Edit.objects.all()
    assert record.field == Edit.EditableField.STATED_TIME
    assert record.edited_at == four_hour_block(record.edited_at)


def test_the_metadata_time_cannot_be_edited(client, uploaded):
    clip, code = uploaded
    edit(client, code, metadata_time='2020-01-01T00:00')
    clip.refresh_from_db()
    assert clip.metadata_time == datetime.datetime(2026, 5, 1, 12, 3, 22, tzinfo=UTC)


def test_old_context_and_contact_details_are_not_kept(client, uploaded):
    clip, code = uploaded
    edit(client, code, context='Second context.', contact_details='')
    assert vault.code_holder_text(clip, vault.CONTEXT, normalise(code)) == 'Second context.'
    assert vault.code_holder_text(clip, vault.CONTACT_DETAILS, normalise(code)) == ''
    assert VaultText.objects.get().kind == vault.CONTEXT
    connection.ensure_connection()
    dump = '\n'.join(connection.connection.iterdump())
    assert 'First context.' not in dump and '@first-test-handle' not in dump
    assert set(Edit.objects.values_list('field', flat=True)) == {'context', 'contact_details'}


def test_a_clip_confirmed_before_encryption_gets_its_code_holder_key_at_its_first_edit(client, uploaded, group_key_pair):
    from tests.conftest import lawyer_text
    clip, code = uploaded
    Clip.objects.filter(pk=clip.pk).update(code_holder_public_key=None)
    VaultText.objects.all().delete()
    edit(client, code, context='Entered again after encryption.')
    clip.refresh_from_db()
    assert bytes(clip.code_holder_public_key) == bytes(vault.code_holder_key(normalise(code)).public_key)
    assert vault.code_holder_text(clip, vault.CONTEXT, normalise(code)) == 'Entered again after encryption.'
    assert lawyer_text(clip, vault.CONTEXT, group_key_pair) == 'Entered again after encryption.'


def test_moving_the_place_keeps_the_as_uploaded_one(client, uploaded):
    clip, code = uploaded
    edit(client, code, latitude=str(ELSEWHERE[0]), longitude=str(ELSEWHERE[1]))
    clip.refresh_from_db()
    assert clip.stated_cell == grid.cell_for_point(*ELSEWHERE, 11)
    assert clip.as_uploaded_stated_cell == grid.cell_for_point(*STATED, 11)
    assert list(Edit.objects.values_list('field', flat=True)) == ['stated_location']


def test_a_vaguer_place_can_be_given_from_the_shown_centre(client, uploaded):
    clip, code = uploaded
    edit(client, code, precision=Precision.NEIGHBOURHOOD)
    clip.refresh_from_db()
    assert grid.resolution(clip.stated_cell) == 9


def test_a_more_precise_place_needs_a_new_position(client, uploaded):
    clip, code = uploaded
    edit(client, code, precision=Precision.NEIGHBOURHOOD)
    response = edit(client, code, precision=Precision.EXACT_SPOT)
    assert response.status_code == 400
    assert 'To make the place more precise, give its new position.' in response.content.decode()
    clip.refresh_from_db()
    assert grid.resolution(clip.stated_cell) == 9
    edit(client, code, precision=Precision.EXACT_SPOT, latitude=str(ELSEWHERE[0]), longitude=str(ELSEWHERE[1]))
    clip.refresh_from_db()
    assert clip.stated_cell == grid.cell_for_point(*ELSEWHERE, 11)


def test_the_place_can_be_removed(client, uploaded):
    clip, code = uploaded
    edit(client, code, latitude='', longitude='', precision=None)
    clip.refresh_from_db()
    assert (clip.stated_cell, clip.stated_precision) == (None, '')
    assert clip.as_uploaded_stated_cell is not None


def test_the_metadata_location_can_only_be_made_coarser(client, uploaded):
    clip, code = uploaded
    html = open_clip(client, code).content.decode()
    assert re.findall(r'name="metadata_precision" value="(\d+)"', html) == ['11', '10', '9']
    edit(client, code, metadata_precision='10')
    clip.refresh_from_db()
    assert clip.metadata_cell == grid.cell_for_point(*FILE_GPS, 10)
    assert re.findall(r'name="metadata_precision" value="(\d+)"', open_clip(client, code).content.decode()) == ['10', '9']
    assert edit(client, code, metadata_precision='11').status_code == 400
    clip.refresh_from_db()
    assert grid.resolution(clip.metadata_cell) == 10


def test_at_the_coarsest_there_is_nothing_to_choose(client, uploaded):
    clip, code = uploaded
    edit(client, code, metadata_precision='9')
    html = open_clip(client, code).content.decode()
    assert 'name="metadata_precision"' not in html
    assert 'About 340 m' in html


def test_tags_can_change_and_lawyer_tags_stay(client, uploaded):
    clip, code = uploaded
    ClipTag.objects.create(clip=clip, key='arrest', source=ClipTag.Source.LAWYER)
    edit(client, code, tags=['tear_gas', 'kettling'], custom_tags='Pont Neuf')
    assert set(ClipTag.objects.values_list('key', 'custom_name', 'source')) == {
        ('tear_gas', '', 'code_holder'), ('kettling', '', 'code_holder'),
        ('', 'Pont Neuf', 'code_holder'), ('arrest', '', 'lawyer'),
    }
    assert Clip.objects.get().as_uploaded_tags == {'default': ['arrest'], 'custom': ['Barricade']}
    assert list(Edit.objects.values_list('field', flat=True)) == ['tags']


def test_a_tag_the_code_holder_keeps_keeps_its_row(client, uploaded):
    # What lawyers did to a tag (removing it for lawyers, proposals) is held on its row, so an
    # edit that keeps the tag must keep the row (SPEC.md §7).
    clip, code = uploaded
    kept = {(tag.key, tag.custom_name): tag.pk for tag in ClipTag.objects.filter(clip=clip)}
    edit(client, code, tags=['arrest', 'kettling'], custom_tags='Barricade, Pont Neuf')
    after = {(tag.key, tag.custom_name): tag.pk for tag in ClipTag.objects.filter(clip=clip)}
    assert after[('arrest', '')] == kept[('arrest', '')]
    assert after[('', 'Barricade')] == kept[('', 'Barricade')]
    assert set(after) == {('arrest', ''), ('kettling', ''), ('', 'Barricade'), ('', 'Pont Neuf')}
    edit(client, code, tags=['kettling'], custom_tags='Pont Neuf')
    assert set(ClipTag.objects.values_list('key', 'custom_name')) == {('kettling', ''), ('', 'Pont Neuf')}
    assert ClipTag.objects.get(key='kettling').pk == after[('kettling', '')]


def test_an_edit_with_a_wrong_code_changes_nothing(client, uploaded):
    clip, code = uploaded
    values = form_values(open_clip(client, code).content.decode())
    values.update(code='7K3Q-M9XA-2B4C-D6EF-8GHJ-0KMN-1PQR', context='Changed by someone else.')
    assert client.post(EDIT_URL, values).status_code == 404
    assert vault.code_holder_text(clip, vault.CONTEXT, normalise(code)) == 'First context.'
    assert not Edit.objects.exists()


def test_deleting_needs_the_box_ticked(client, uploaded):
    clip, code = uploaded
    assert client.post(DELETE_URL, {'code': code}).status_code == 400
    assert Clip.objects.exists()


def test_deleting_removes_the_clip_and_everything_it_held(
    client, uploaded, settings, django_capture_on_commit_callbacks,
):
    clip, code = uploaded
    edit(client, code, context='Second context.')
    file_path = settings.QUARANTINE_DIR / Original.objects.get().file.name
    assert file_path.exists()
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(DELETE_URL, {'code': code, 'confirm_delete': 'yes'}, headers=ENGLISH)
    assert response.status_code == 200
    assert 'Its upload code no longer works.' in response.content.decode()
    for model in (Clip, ClipTag, Edit, Original, VaultText):
        assert not model.objects.exists()
    assert not file_path.exists()
    assert open_clip(client, code).status_code == 404


def test_these_pages_switch_language_back_to_the_code_entry(client, uploaded):
    clip, code = uploaded
    html = open_clip(client, code).content.decode()
    assert f'name="next" value="{ENTRY_URL}"' in html


def test_the_code_card_says_where_to_come_back(client, make_media):
    video = make_media('plain.mp4', *H264, '-an')
    with open(video, 'rb') as file:
        confirm_url = client.post(reverse('clips:upload'), {'file': file})['Location']
    html = client.post(confirm_url, {}).content.decode()
    assert f'href="http://testserver{ENTRY_URL}"' in html


def test_a_clip_being_prepared_says_so(client, uploaded):
    clip, code = uploaded
    html = open_clip(client, code).content.decode()
    assert 'Processing' in html and 'being prepared' in html


def test_a_ready_clip_says_so(client, uploaded):
    clip, code = uploaded
    Clip.objects.filter(pk=clip.pk).update(status=Clip.Status.READY)
    html = open_clip(client, code).content.decode()
    assert 'Ready' in html and 'being prepared' not in html
    assert f'action="{EDIT_URL}"' in html


def test_a_failed_clip_asks_for_a_new_upload_and_can_only_be_deleted(client, uploaded):
    clip, code = uploaded
    Clip.objects.filter(pk=clip.pk).update(status=Clip.Status.FAILED)
    response = open_clip(client, code)
    html = response.content.decode()
    assert 'could not prepare your clip' in html
    assert reverse('clips:upload') in html
    assert f'action="{EDIT_URL}"' not in html
    assert 'action="{}"'.format(reverse('clips:delete')) in html

    refused = client.post(EDIT_URL, {'code': code, 'stated_time': '2026-05-02T10:00'}, headers=ENGLISH)
    assert refused.status_code == 409
    clip.refresh_from_db()
    assert clip.stated_time.day == 1
    assert not clip.edits.exists()

    deleted = client.post(reverse('clips:delete'), {'code': code, 'confirm_delete': 'yes'})
    assert deleted.status_code == 200
    assert not Clip.objects.exists()


def test_the_failed_message_is_in_french(client, uploaded):
    clip, code = uploaded
    Clip.objects.filter(pk=clip.pk).update(status=Clip.Status.FAILED)
    html = client.post(ENTRY_URL, {'code': code}, headers={'accept_language': 'fr'}).content.decode()
    assert 'Échec de la préparation' in html
    assert "Nous n'avons pas pu préparer votre clip" in html


# An edit racing ingest (SPEC.md §9): the clip the request loaded may have changed before it is saved.

def changing_meanwhile(monkeypatch, **fields):
    """Change the clip's row once the edit has loaded it, as ingest or give_up would."""
    from clips import editing
    real = editing.code_holder_tags

    def and_meanwhile(clip):
        Clip.objects.filter(pk=clip.pk).update(**fields)
        return real(clip)

    monkeypatch.setattr(editing, 'code_holder_tags', and_meanwhile)


def test_an_edit_keeps_what_ingest_recorded_meanwhile(client, uploaded, monkeypatch):
    clip, code = uploaded
    Clip.objects.filter(pk=clip.pk).update(status=Clip.Status.PROCESSING, duration=None)
    changing_meanwhile(monkeypatch, status=Clip.Status.READY, duration=datetime.timedelta(seconds=42), width=320)
    response = edit(client, code, stated_time='2026-05-01T14:30')
    assert response.status_code == 200 and response.context['clip'].status == Clip.Status.READY
    clip.refresh_from_db()
    assert (clip.status, clip.duration, clip.width) == (Clip.Status.READY, datetime.timedelta(seconds=42), 320)
    assert clip.stated_time == datetime.datetime(2026, 5, 1, 12, 30, tzinfo=UTC)


def test_an_edit_does_not_bring_back_a_clip_that_failed_meanwhile(client, uploaded, monkeypatch):
    clip, code = uploaded
    Clip.objects.filter(pk=clip.pk).update(status=Clip.Status.PROCESSING)
    VaultText.objects.all().delete()
    changing_meanwhile(monkeypatch, status=Clip.Status.FAILED)
    response = edit(client, code, context='Written after it failed.')
    assert response.status_code == 409
    clip.refresh_from_db()
    # Nothing is left to describe: its texts went with it (SPEC.md §9).
    assert clip.status == Clip.Status.FAILED and not VaultText.objects.exists() and not Edit.objects.exists()
