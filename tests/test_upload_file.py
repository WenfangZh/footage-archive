import datetime
import re
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.uploadhandler import StopUpload
from django.test import Client
from django.urls import reverse
from django.utils import timezone, translation

from clips import uploads
from clips.models import Clip, Original
from clips.uploads import SizeCapUploadHandler, size_limit_display
from tests.media import H264

pytestmark = pytest.mark.django_db

UPLOAD_URL = reverse('clips:upload')
ENGLISH = {'accept_language': 'en'}


def quarantine_files(settings):
    return {path for path in settings.QUARANTINE_DIR.rglob('*') if path.is_file()}


@pytest.fixture
def new_quarantine_files(settings):
    """The files a test leaves in quarantine, ignoring those other tests left there."""
    before = quarantine_files(settings)
    return lambda: quarantine_files(settings) - before


@pytest.fixture
def video(make_media):
    return make_media(
        'phone.mp4', *H264, '-c:a', 'aac',
        '-metadata', 'creation_time=2026-05-01T12:03:22Z', '-metadata', 'location=+48.8532+002.3692/',
    )


def post_file(client, path, **extra):
    with open(path, 'rb') as file:
        return client.post(UPLOAD_URL, {**extra, 'file': file}, headers=ENGLISH)


def test_the_form_states_the_limit_and_checks_it_in_the_browser(client):
    response = client.get(UPLOAD_URL, headers=ENGLISH)
    html = response.content.decode()
    assert response.status_code == 200
    assert 'Maximum size: 2 GB.' in html
    assert 'data-max-bytes="2000000000"' in html
    assert 'accept="video/*"' in html
    assert '/static/clips/upload.js' in html


def test_the_csrf_token_comes_before_the_file(client):
    html = client.get(UPLOAD_URL).content.decode()
    assert html.index('csrfmiddlewaretoken') < html.index('type="file"')


def test_a_video_goes_to_quarantine_unchanged(client, video, settings, new_quarantine_files):
    response = post_file(client, video)
    original = Original.objects.get()
    assert response.status_code == 302
    assert response['Location'] == reverse('clips:confirm', args=[original.id])
    assert original.clip is None
    [stored] = new_quarantine_files()
    assert stored == settings.QUARANTINE_DIR / original.file.name
    assert stored.read_bytes() == video.read_bytes()
    assert stored.stat().st_mode & 0o777 == 0o600


def test_the_uploaders_file_name_is_not_kept(client, video, new_quarantine_files):
    client.post(UPLOAD_URL, {'file': SimpleUploadedFile('IMG_1234 Rue X.MOV', video.read_bytes())})
    original = Original.objects.get()
    assert re.fullmatch('[0-9a-f]{32}', original.file.name)
    assert not any('IMG' in path.name or 'Rue' in path.name for path in new_quarantine_files())


@pytest.mark.parametrize('content', [b'hello, not a video', b'\xff\xd8\xff\xe0 fake jpeg'])
def test_a_file_that_is_not_a_video_is_deleted(client, tmp_path, content, new_quarantine_files):
    path = tmp_path / 'upload.mp4'
    path.write_bytes(content)
    response = post_file(client, path)
    assert response.status_code == 400
    assert 'not a video we can accept' in response.content.decode()
    assert not Original.objects.exists() and not Clip.objects.exists()
    assert new_quarantine_files() == set()


def test_a_picture_is_refused(client, make_media, new_quarantine_files):
    response = post_file(client, make_media('photo.jpg', '-frames:v', '1', '-an'))
    assert response.status_code == 400
    assert not Original.objects.exists()
    assert new_quarantine_files() == set()


def test_a_post_without_a_file_is_refused(client):
    response = client.post(UPLOAD_URL, {}, headers=ENGLISH)
    assert response.status_code == 400
    assert not Original.objects.exists()


def test_two_files_at_once_are_refused(client, video):
    with open(video, 'rb') as first, open(video, 'rb') as second:
        response = client.post(UPLOAD_URL, {'file': [first, second]})
    assert response.status_code == 400
    assert not Original.objects.exists()


def test_a_file_over_the_cap_is_stopped_while_it_arrives(client, video, settings, new_quarantine_files):
    settings.UPLOAD_MAX_BYTES = video.stat().st_size // 2
    response = post_file(client, video)
    assert response.status_code == 413
    assert 'so it was refused. Nothing was kept.' in response.content.decode()
    assert not Original.objects.exists()
    assert new_quarantine_files() == set()


def test_a_file_declared_over_the_cap_is_stopped_before_it_arrives(
    client, video, settings, monkeypatch, new_quarantine_files,
):
    settings.UPLOAD_MAX_BYTES = video.stat().st_size
    monkeypatch.setattr(uploads, 'FORM_OVERHEAD_BYTES', 0)
    response = post_file(client, video)
    assert response.status_code == 413
    assert not Original.objects.exists()
    assert new_quarantine_files() == set()


def test_a_file_exactly_at_the_cap_is_accepted(client, video, settings):
    settings.UPLOAD_MAX_BYTES = video.stat().st_size
    assert post_file(client, video).status_code == 302


def test_an_oversize_file_gets_our_page_even_with_csrf_checks(video, settings):
    # The token is read before the file is stopped, so the answer is 413, not a CSRF failure.
    client = Client(enforce_csrf_checks=True)
    client.get(UPLOAD_URL)
    settings.UPLOAD_MAX_BYTES = video.stat().st_size // 2
    response = post_file(client, video, csrfmiddlewaretoken=client.cookies['csrftoken'].value)
    assert response.status_code == 413


def test_the_handler_passes_chunks_on_under_the_cap(settings):
    settings.UPLOAD_MAX_BYTES = 10
    handler = SizeCapUploadHandler(request=SimpleNamespace())
    handler.handle_raw_input(None, {}, 100, b'', None)
    assert handler.receive_data_chunk(b'12345', 0) == b'12345'
    assert handler.receive_data_chunk(b'67890', 5) == b'67890'
    with pytest.raises(StopUpload):
        handler.receive_data_chunk(b'!', 10)
    assert handler.request.upload_too_large


@pytest.mark.parametrize('limit, english', [
    (2_000_000_000, '2 GB'), (1_500_000_000, '1.5 GB'), (50_000_000, '50 MB'), (900, '900 bytes'),
])
def test_size_limit_display(settings, limit, english):
    settings.UPLOAD_MAX_BYTES = limit
    with translation.override('en'):
        assert size_limit_display() == english


def test_step_2_shows_what_the_quick_read_found(client, video):
    post_file(client, video)
    response = client.get(reverse('clips:confirm', args=[Original.objects.get().id]), headers=ENGLISH)
    html = response.content.decode()
    assert response.status_code == 200
    assert 'The video contains a GPS position.' in html
    # Neither the page nor the browser's cache keeps it.
    assert 'no-store' in response['Cache-Control']


def test_step_2_is_gone_once_the_form_is_confirmed_or_too_old(client, video, settings):
    post_file(client, video)
    original = Original.objects.get()
    url = reverse('clips:confirm', args=[original.id])
    original.received_at = timezone.now() - datetime.timedelta(minutes=settings.UNCONFIRMED_ORIGINAL_MINUTES + 1)
    original.save()
    assert client.get(url).status_code == 404
    original.received_at = timezone.now()
    original.clip = Clip.objects.create(code_hash='0' * 64)
    original.save()
    assert client.get(url).status_code == 404


def test_step_2_for_an_unknown_original_is_not_found(client):
    assert client.get(reverse('clips:confirm', args=['00000000-0000-4000-8000-000000000000'])).status_code == 404


def test_quarantine_is_never_served(client, video):
    post_file(client, video)
    name = Original.objects.get().file.name
    for url in (f'/var/quarantine/{name}', f'/quarantine/{name}', f'/media/{name}', f'/{name}'):
        assert client.get(url).status_code == 404
