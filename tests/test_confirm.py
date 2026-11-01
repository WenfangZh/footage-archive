import datetime
import re

import pytest
from django.db import connection
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone
from django.views.debug import SafeExceptionReporterFilter

from clips import confirmation, grid, upload_codes, vault, views
from clips.models import Clip, ClipTag, Original, Precision
from clips.probe import quick_read
from clips.time_blocks import four_hour_block
from tests.media import H264

# Texts are sealed to the group keys in use when they are written: one exists in each test here.
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('group_key_pair')]

UTC = datetime.UTC
ENGLISH = {'accept_language': 'en'}
# Fake GPS of a public place, in the synthetic video; the form states another one.
FILE_GPS = (48.8532, 2.3692)  # Place de la Bastille
STATED = (48.8675, 2.3638)  # Place de la République


@pytest.fixture
def video(make_media):
    return make_media(
        'phone.mp4', *H264, '-c:a', 'aac',
        '-metadata', 'creation_time=2026-05-01T12:03:22Z', '-metadata', 'location=+48.8532+002.3692/',
    )


@pytest.fixture
def video_without_metadata(make_media):
    return make_media('plain.mp4', *H264, '-an')


def start_upload(client, path):
    """Upload step 1; returns the URL of step 2."""
    with open(path, 'rb') as file:
        response = client.post(reverse('clips:upload'), {'file': file})
    assert response.status_code == 302
    return response['Location']


def full_form(**changes):
    data = {
        'stated_time': '2026-05-01T14:05',
        'latitude': str(STATED[0]), 'longitude': str(STATED[1]), 'precision': Precision.STREET,
        'metadata_precision': '11',
        'tags': ['arrest', 'kettling'],
        'custom_tags': 'Pont Neuf, Barricade',
        'context': 'Filmed from across the street.',
        'contact_details': '@someone-test-handle',
    }
    data.update(changes)
    return {key: value for key, value in data.items() if value is not None}


def code_on(html):
    return re.search(r'<code>([0-9A-Z-]+)</code>', html)[1]


def test_the_form_is_prefilled_from_the_quick_read(client, video):
    html = client.get(start_upload(client, video), headers=ENGLISH).content.decode()
    # 12:03:22 UTC is 14:03 in Paris in May.
    assert 'value="2026-05-01T14:03"' in html
    assert 'value="48.8532"' in html and 'value="2.3692"' in html
    assert re.search(r'value="exact_spot"[^>]*checked', html)
    assert re.search(r'name="metadata_precision" value="11"[^>]*checked', html)
    assert 'It is kept as found, and nobody can change it.' in html
    assert 'name="metadata_time"' not in html


def test_without_gps_there_is_no_metadata_precision_to_choose(client, video_without_metadata):
    html = client.get(start_upload(client, video_without_metadata), headers=ENGLISH).content.decode()
    assert 'metadata_precision' not in html
    assert not re.search(r'value="exact_spot"[^>]*checked', html)


def test_confirming_creates_the_clip_and_shows_its_code(client, video):
    url = start_upload(client, video)
    response = client.post(url, full_form(), headers=ENGLISH)
    html = response.content.decode()
    assert response.status_code == 200
    assert 'no-store' in response['Cache-Control']
    code = upload_codes.normalise(code_on(html))

    clip = Clip.objects.get()
    assert clip.code_hash == upload_codes.keyed_hash(code)
    assert clip.metadata_time == datetime.datetime(2026, 5, 1, 12, 3, 22, tzinfo=UTC)
    assert clip.stated_time == datetime.datetime(2026, 5, 1, 12, 5, tzinfo=UTC)
    # A street: the finest cells of the hexagon about 130 m across (ADR 0009).
    assert clip.stated_cells == sorted(grid.children(grid.cell_for_point(*STATED, 10), 11))
    assert clip.metadata_cell == grid.cell_for_point(*FILE_GPS, 11)
    assert (clip.as_uploaded_stated_time, clip.as_uploaded_stated_cells) == (clip.stated_time, clip.stated_cells)
    assert clip.as_uploaded_tags == {'default': ['arrest', 'kettling'], 'custom': ['Pont Neuf', 'Barricade']}
    assert {(tag.key, tag.custom_name, tag.source) for tag in ClipTag.objects.all()} == {
        ('arrest', '', 'code_holder'), ('kettling', '', 'code_holder'),
        ('', 'Pont Neuf', 'code_holder'), ('', 'Barricade', 'code_holder'),
    }
    # Only the code opens them for the code holder (docs/m5-encryption-design.md §7.2).
    assert vault.code_holder_text(clip, vault.CONTEXT, code) == 'Filmed from across the street.'
    assert vault.code_holder_text(clip, vault.CONTACT_DETAILS, code) == '@someone-test-handle'
    original = Original.objects.get()
    assert original.clip == clip
    assert original.received_at == four_hour_block(original.received_at)


def test_the_database_holds_no_plaintext_of_the_context_or_contact_details(client, video, group_key_pair):
    from tests.conftest import lawyer_text
    client.post(start_upload(client, video), full_form())
    connection.ensure_connection()
    dump = '\n'.join(connection.connection.iterdump())
    for text in ('Filmed from across the street', 'someone-test-handle'):
        assert text not in dump and text.encode().hex() not in dump.lower()
    # Lawyers read them with the group key.
    assert lawyer_text(Clip.objects.get(), vault.CONTEXT, group_key_pair) == 'Filmed from across the street.'


def test_the_code_is_stored_nowhere(client, video):
    response = client.post(start_upload(client, video), full_form())
    shown = code_on(response.content.decode())
    connection.ensure_connection()
    dump = '\n'.join(connection.connection.iterdump())
    assert Clip.objects.exists()
    assert shown not in dump and upload_codes.normalise(shown) not in dump


def test_the_metadata_time_comes_from_the_file_not_the_form(client, video):
    client.post(start_upload(client, video), full_form(metadata_time='2020-01-01T00:00'))
    assert Clip.objects.get().metadata_time == datetime.datetime(2026, 5, 1, 12, 3, 22, tzinfo=UTC)


def test_vague_precisions_keep_only_coarse_places(client, video):
    client.post(start_upload(client, video), full_form(precision=Precision.NEIGHBOURHOOD, metadata_precision='9'))
    clip = Clip.objects.get()
    assert {grid.coarsen(cell, 9) for cell in clip.stated_cells} == {grid.cell_for_point(*STATED, 9)}
    assert len(clip.stated_cells) == 49
    assert clip.metadata_cell == grid.cell_for_point(*FILE_GPS, 9)


@pytest.mark.parametrize('changes', [
    {'metadata_precision': '12'},
    {'metadata_precision': '8'},
    {'longitude': None},
    {'latitude': None},
    {'precision': None},
    {'latitude': '91'},
    {'tags': ['suspect']},
    {'custom_tags': 'x' * 61},
    {'custom_tags': ','.join(f'tag {n}' for n in range(11))},
    {'stated_time': 'last Tuesday'},
    {'contact_details': 'x' * 501},
])
def test_invalid_answers_are_refused_and_create_nothing(client, video, changes):
    response = client.post(start_upload(client, video), full_form(**changes))
    assert response.status_code == 400
    assert not Clip.objects.exists()
    assert Original.objects.get().clip is None


def test_custom_tags_are_trimmed_and_deduplicated(client, video):
    client.post(start_upload(client, video), full_form(custom_tags=' Pont Neuf, ,Pont Neuf,  Barricade '))
    assert Clip.objects.get().as_uploaded_tags['custom'] == ['Pont Neuf', 'Barricade']


def test_an_empty_form_still_creates_a_clip_with_a_warning(client, video_without_metadata):
    response = client.post(start_upload(client, video_without_metadata), {}, headers=ENGLISH)
    html = response.content.decode()
    assert response.status_code == 200
    assert 'No lawyer can find this clip until you add the time and the place.' in html
    clip = Clip.objects.get()
    assert (clip.metadata_time, clip.metadata_cell, clip.stated_time, clip.stated_cells) == (None,) * 4
    assert clip.as_uploaded_tags == {'default': [], 'custom': []}


def test_a_complete_form_has_no_warning(client, video):
    html = client.post(start_upload(client, video), full_form(), headers=ENGLISH).content.decode()
    assert 'No lawyer can find this clip' not in html


def test_the_code_card_has_no_form_and_never_sends_the_card_label(client, video):
    html = client.post(start_upload(client, video), full_form(), headers=ENGLISH).content.decode()
    # No form at all, not even the language switch, which would reload the page.
    assert '<form' not in html
    label_input = re.search(r'<input[^>]*id="card-label"[^>]*>', html)[0]
    assert 'name=' not in label_input
    assert 'value="05/01/2026 – Arrest, Kettling (crowd encircled and held by police), Pont Neuf"' in label_input
    assert "Anyone who finds this card can read the clip's messages and delete it." in html


def test_confirming_twice_shows_that_it_was_already_sent(client, video):
    url = start_upload(client, video)
    client.post(url, full_form())
    second = client.post(url, full_form(), headers=ENGLISH)
    assert second.status_code == 410
    assert 'This video was already sent' in second.content.decode()
    assert Clip.objects.count() == 1
    assert client.get(url).status_code == 410


def test_of_two_racing_confirmations_only_one_creates_a_clip(client, video):
    start_upload(client, video)
    original = Original.objects.get()
    read = quick_read(original.file.path)
    form = views.ConfirmForm(full_form(), has_gps=True)
    assert form.is_valid()
    confirmation.confirm_upload(Original.objects.get(), read, form.cleaned_data)
    with pytest.raises(confirmation.AlreadyConfirmed):
        confirmation.confirm_upload(original, read, form.cleaned_data)  # the stale copy
    assert Clip.objects.count() == 1


def test_an_expired_original_cannot_be_confirmed(client, video, settings):
    url = start_upload(client, video)
    Original.objects.update(
        received_at=timezone.now() - datetime.timedelta(minutes=settings.UNCONFIRMED_ORIGINAL_MINUTES + 1),
    )
    assert client.post(url, full_form()).status_code == 404
    assert not Clip.objects.exists()


def test_error_reports_never_show_what_the_uploader_typed(client, video, monkeypatch):
    url = start_upload(client, video)
    request = RequestFactory().post(url, full_form())
    request._dont_enforce_csrf_checks = True

    def fail(*args, **kwargs):
        raise RuntimeError('simulated failure')

    monkeypatch.setattr(views, 'confirm_upload', fail)
    with pytest.raises(RuntimeError):
        views.confirm(request, original_id=Original.objects.get().id)
    shown = SafeExceptionReporterFilter().get_post_parameters(request)
    for field in ('latitude', 'longitude', 'custom_tags', 'context', 'contact_details'):
        assert shown[field] == SafeExceptionReporterFilter.cleansed_substitute
