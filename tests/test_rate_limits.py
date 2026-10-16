import re
import time

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.urls import reverse

from clips.models import Original
from tests.media import H264

pytestmark = pytest.mark.django_db

ENGLISH = {'accept_language': 'en'}
UPLOAD_URL = reverse('clips:upload')
ENTRY_URL = reverse('clips:code_holder')
WRONG_CODE = '7K3Q-M9XA-2B4C-D6EF-8GHJ-0KMN-1PQR'
OTHER_ADDRESS = {'REMOTE_ADDR': '192.0.2.7'}  # A documentation address (RFC 5737).


@pytest.fixture
def video(make_media):
    return make_media('plain.mp4', *H264, '-an')


@pytest.fixture
def code(client, video):
    with open(video, 'rb') as file:
        confirm_url = client.post(UPLOAD_URL, {'file': file})['Location']
    html = client.post(confirm_url, {}).content.decode()
    return re.search(r'<code>([0-9A-Z-]+)</code>', html)[1]


def upload(client, path, **extra):
    with open(path, 'rb') as file:
        return client.post(UPLOAD_URL, {'file': file}, headers=ENGLISH, **extra)


def test_uploads_over_the_limit_are_refused_before_the_file_is_read(client, video, settings):
    settings.RATE_LIMIT_UPLOADS = 2
    assert [upload(client, video).status_code for _ in range(2)] == [302, 302]
    response = upload(client, video)
    assert response.status_code == 429
    assert 'Too many attempts' in response.content.decode()
    assert response['Retry-After'] == '3600'
    assert Original.objects.count() == 2
    assert not any(settings.FILE_UPLOAD_TEMP_DIR.iterdir())


def test_every_upload_attempt_counts_even_a_refused_one(client, tmp_path, video, settings):
    settings.RATE_LIMIT_UPLOADS = 1
    not_a_video = tmp_path / 'notes.txt'
    not_a_video.write_text('not a video')
    assert upload(client, not_a_video).status_code == 400
    assert upload(client, video).status_code == 429


def test_each_connection_has_its_own_count(client, video, settings):
    settings.RATE_LIMIT_UPLOADS = 1
    assert upload(client, video).status_code == 302
    assert upload(client, video).status_code == 429
    assert upload(client, video, **OTHER_ADDRESS).status_code == 302


def test_the_upload_page_itself_is_never_limited(client, video, settings):
    settings.RATE_LIMIT_UPLOADS = 1
    upload(client, video)
    assert client.get(UPLOAD_URL).status_code == 200


def test_wrong_codes_over_the_limit_stop_all_code_checks(client, code, settings):
    settings.RATE_LIMIT_FAILED_CODES = 3
    assert client.post(ENTRY_URL, {'code': code}).status_code == 200
    statuses = [client.post(ENTRY_URL, {'code': WRONG_CODE}).status_code for _ in range(3)]
    assert statuses == [404, 404, 404]
    # Now even the right code is not checked, on every page that takes one.
    assert client.post(ENTRY_URL, {'code': code}).status_code == 429
    assert client.post(reverse('clips:edit'), {'code': code}).status_code == 429
    assert client.post(reverse('clips:delete'), {'code': code, 'confirm_delete': 'yes'}).status_code == 429
    assert client.post(ENTRY_URL, {'code': code}, **OTHER_ADDRESS).status_code == 200


def test_wrong_codes_count_on_the_edit_and_delete_pages_too(client, code, settings):
    settings.RATE_LIMIT_FAILED_CODES = 2
    client.post(reverse('clips:edit'), {'code': WRONG_CODE})
    client.post(reverse('clips:delete'), {'code': WRONG_CODE})
    assert client.post(ENTRY_URL, {'code': code}).status_code == 429


def test_right_codes_do_not_count(client, code, settings):
    settings.RATE_LIMIT_FAILED_CODES = 1
    for _ in range(5):
        assert client.post(ENTRY_URL, {'code': code}).status_code == 200


def test_the_address_is_kept_only_as_a_keyed_hash(client, code):
    client.post(ENTRY_URL, {'code': WRONG_CODE}, **OTHER_ADDRESS)
    keys = list(cache._cache)
    assert keys and not any('192.0.2.7' in key for key in keys)
    connection.ensure_connection()
    assert '192.0.2.7' not in '\n'.join(connection.connection.iterdump())


def test_counts_expire_with_their_window(client, code, settings):
    settings.RATE_LIMIT_WINDOW_MINUTES = 15
    client.post(ENTRY_URL, {'code': WRONG_CODE})
    client.post(ENTRY_URL, {'code': WRONG_CODE})  # Counting again does not extend the window.
    [expires] = [when for key, when in cache._expire_info.items() if 'failed_codes' in key]
    assert abs(expires - time.time() - 15 * 60) < 5


def test_an_upload_over_the_limit_is_refused_before_the_csrf_check(video, settings):
    # The CSRF check reads the whole body; a refusal that comes first never reads the file.
    settings.RATE_LIMIT_UPLOADS = 1
    client = Client(enforce_csrf_checks=True)
    assert upload(client, video).status_code == 403  # Counted, then refused for its missing token.
    assert upload(client, video).status_code == 429
