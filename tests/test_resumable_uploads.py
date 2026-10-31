"""Resumable uploads (SPEC.md §8, M6.3): a file sent in pieces through the tus protocol, cut off
and resumed, arrives byte for byte and goes on to the form, the code and ingest as before;
nothing beyond the size cap or the declared length is stored; a finished file that is not a
video is deleted; partial uploads are never served, keep nothing sent with them, are deleted
once their time is up and never appear in a log; starting one counts towards the rate limit
and is refused while uploads are closed."""

import datetime
import fcntl
import io
import logging
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from clips import cleanup, hashing
from clips.models import Clip, HashRecord, Original, PartialUpload
from tests.media import H264
from tests.test_confirm import code_on, full_form

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('group_key_pair')]

START_URL = reverse('clips:resumable_start')
TUS = {'Tus-Resumable': '1.0.0'}
PIECE_TYPE = 'application/offset+octet-stream'
ENGLISH = {'Accept-Language': 'en'}


@pytest.fixture
def video(make_media):
    return make_media(
        'phone.mp4', *H264, '-c:a', 'aac',
        '-metadata', 'creation_time=2026-05-01T12:03:22Z', '-metadata', 'location=+48.8532+002.3692/',
    )


@pytest.fixture
def new_quarantine_files(settings):
    def files():
        return {path for path in settings.QUARANTINE_DIR.rglob('*') if path.is_file()}
    before = files()
    return lambda: files() - before


class CutOff(io.BytesIO):
    """A request body whose connection drops once these bytes are read."""

    def read(self, size=-1):
        data = super().read(size)
        if not data:
            raise ConnectionResetError
        return data


def start(client, length, **headers):
    return client.post(START_URL, headers={**TUS, 'Upload-Length': str(length), **headers})


def address(response):
    assert response.status_code == 201
    return response['Location']


def send(client, url, offset, data, **extra):
    return client.generic(
        'PATCH', url, data, content_type=PIECE_TYPE, headers={**TUS, 'Upload-Offset': str(offset)}, **extra,
    )


def offset(client, url):
    response = client.head(url, headers=TUS)
    assert response.status_code == 200
    return int(response['Upload-Offset'])


def finish(client, url):
    return client.post(url.removeprefix('http://testserver') + 'finish/', headers=ENGLISH)


def test_a_file_cut_off_and_resumed_arrives_byte_for_byte_and_goes_on_to_the_code_and_ingest(
    client, video, django_capture_on_commit_callbacks,
):
    data = video.read_bytes()
    url = address(start(client, len(data)))
    half = len(data) // 2

    # The connection drops a little way into the second piece: what arrived is kept.
    send(client, url, 0, data[:half])
    cut_off = {'CONTENT_TYPE': PIECE_TYPE, 'CONTENT_LENGTH': str(len(data) - half), 'wsgi.input': CutOff(data[half:half + 1000])}
    send(client, url, half, b'', **cut_off)
    resumed_at = offset(client, url)
    assert resumed_at == half + 1000
    response = send(client, url, resumed_at, data[resumed_at:])
    assert response.status_code == 204 and response['Upload-Offset'] == str(len(data))

    form_url = finish(client, url)['Location']
    assert Original.objects.get().file.read() == data
    assert not PartialUpload.objects.exists()
    with django_capture_on_commit_callbacks(execute=True):
        code_card = client.post(form_url, full_form(), headers=ENGLISH)

    assert code_on(code_card.content.decode())
    clip = Clip.objects.get()
    assert clip.status == Clip.Status.READY
    assert HashRecord.objects.get(clip=clip).original_sha256 == hashing.file_hash(video)


def test_an_upload_declared_over_the_cap_is_refused_and_nothing_is_stored(client, settings, new_quarantine_files):
    settings.UPLOAD_MAX_BYTES = 1000

    assert start(client, 1001).status_code == 413
    assert address(start(client, 1000))
    assert PartialUpload.objects.count() == 1 and len(new_quarantine_files()) == 1


def test_a_piece_past_the_declared_length_is_refused_before_any_of_it_is_stored(client, settings):
    settings.UPLOAD_MAX_BYTES = 1000
    url = address(start(client, 1000))
    send(client, url, 0, b'x' * 600)

    assert send(client, url, 600, b'x' * 401).status_code == 413
    assert offset(client, url) == 600


def test_a_piece_at_the_wrong_offset_is_refused(client):
    url = address(start(client, 1000))
    send(client, url, 0, b'x' * 100)

    for wrong in (0, 99, 101):
        assert send(client, url, wrong, b'y' * 10).status_code == 409
    assert offset(client, url) == 100
    assert PartialUpload.objects.get().file.read() == b'x' * 100


def test_two_pieces_of_one_upload_are_never_written_at_once(client):
    url = address(start(client, 1000))
    with open(PartialUpload.objects.get().file.path, 'ab') as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        assert send(client, url, 0, b'x' * 10).status_code == 423
    assert offset(client, url) == 0


@pytest.mark.parametrize('content', [b'', b'not a video at all', b'\x00' * 2048], ids=['one byte', 'text', 'zeros'])
def test_a_finished_upload_that_is_not_a_video_is_deleted_at_once_and_gets_no_code(client, content, new_quarantine_files):
    data = content or b'x'
    url = address(start(client, len(data)))
    send(client, url, 0, data)

    response = finish(client, url)

    assert response.status_code == 400
    assert 'It was deleted, and nothing was kept.' in response.content.decode()
    assert not PartialUpload.objects.exists() and not Original.objects.exists() and not Clip.objects.exists()
    assert not new_quarantine_files()


def test_an_unfinished_upload_cannot_go_on_to_the_form(client, video):
    url = address(start(client, video.stat().st_size))
    send(client, url, 0, video.read_bytes()[:1000])

    assert finish(client, url).status_code == 409
    assert not Original.objects.exists()


def test_a_partial_upload_is_never_served(client, video):
    data = video.read_bytes()
    url = address(start(client, len(data)))
    send(client, url, 0, data)

    assert client.get(url).status_code == 405
    head = client.head(url, headers=TUS)
    assert head.content == b'' and head['Cache-Control'] == 'no-store'


def test_a_partial_upload_keeps_nothing_sent_with_it(client, video):
    # tus clients put the file name in Upload-Metadata, base64-encoded: "filename protest-bastille.mp4".
    url = address(start(client, 1000, **{'Upload-Metadata': 'filename cHJvdGVzdC1iYXN0aWxsZS5tcDQ=,filetype dmlkZW8vbXA0'}))

    partial = PartialUpload.objects.get()
    assert (partial.length, partial.file.size) == (1000, 0)
    assert partial.file.name not in url and str(partial.id) not in partial.file.name
    assert re.fullmatch(r'[0-9a-f]{32}', partial.file.name)


def test_a_partial_upload_is_deleted_once_its_time_is_up_and_answers_as_unknown_from_then(client, settings, new_quarantine_files):
    settings.PARTIAL_UPLOAD_MINUTES = 120
    url = address(start(client, 1000))
    send(client, url, 0, b'x' * 10)
    started = PartialUpload.objects.get().started_at
    up = started + datetime.timedelta(minutes=120)

    assert cleanup.delete_expired_partial_uploads(now=up - datetime.timedelta(seconds=1)) == 0
    PartialUpload.objects.update(started_at=timezone.now() - datetime.timedelta(minutes=120))
    assert client.head(url, headers=TUS).status_code == 404
    assert send(client, url, 10, b'x').status_code == 404

    assert cleanup.delete_expired_partial_uploads() == 1
    assert not PartialUpload.objects.exists() and not new_quarantine_files()


def test_a_piece_arriving_as_its_upload_is_deleted_does_not_bring_it_back(client, new_quarantine_files):
    url = address(start(client, 1000))
    partial = PartialUpload.objects.get()
    # Deleted for age between the request's look-up of the row and its opening of the file.
    partial.file.delete(save=False)

    assert send(client, url, 0, b'x' * 10).status_code == 404
    assert client.head(url, headers=TUS).status_code == 404
    assert finish(client, url).status_code == 404
    assert not new_quarantine_files()


def test_an_abandoned_upload_is_deleted(client, new_quarantine_files):
    url = address(start(client, 1000))
    send(client, url, 0, b'x' * 10)

    assert client.delete(url, headers=TUS).status_code == 204
    assert not PartialUpload.objects.exists() and not new_quarantine_files()
    assert client.head(url, headers=TUS).status_code == 404


def test_the_stray_file_sweep_leaves_a_partial_upload_alone(client, settings):
    settings.PARTIAL_UPLOAD_MINUTES = 600
    address(start(client, 1000))
    later = timezone.now() + datetime.timedelta(minutes=settings.UNCONFIRMED_ORIGINAL_MINUTES + 1)

    cleanup.delete_unconfirmed_originals(now=later)
    assert PartialUpload.objects.get().file.storage.exists(PartialUpload.objects.get().file.name)


def test_no_log_line_names_a_partial_uploads_address(client, settings):
    # Django writes its request warnings and the development server's lines only in development.
    settings.DEBUG = True
    written = io.StringIO()
    handlers = [
        handler for handler in [*logging.getLogger('django').handlers, *logging.getLogger('django.server').handlers]
        if isinstance(handler, logging.StreamHandler)
    ]
    streams = [handler.setStream(written) for handler in handlers]
    try:
        url = address(start(client, 1000))
        path = url.removeprefix('http://testserver')
        assert send(client, url, 5, b'x').status_code == 409  # Logged: "Conflict: <path>".
        assert client.head(path + 'x', headers=TUS).status_code == 404  # Logged: "Not Found: <path>x".
        logging.getLogger('django.server').info('"%s %s %s" %s %s', 'PATCH', path, 'HTTP/1.1', '204', '0')
    finally:
        for handler, stream in zip(handlers, streams):
            handler.setStream(stream)

    log = written.getvalue()
    assert 'Conflict: /upload/resumable/…/' in log and '"PATCH /upload/resumable/…/ HTTP/1.1" 204' in log
    assert str(PartialUpload.objects.get().id) not in log


def test_starting_a_partial_upload_counts_towards_the_rate_limit(client, settings):
    settings.RATE_LIMIT_UPLOADS = 2
    address(start(client, 1000))
    address(start(client, 1000))

    assert start(client, 1000).status_code == 429
    assert PartialUpload.objects.count() == 2


def test_pieces_and_progress_checks_do_not_count(client, settings):
    settings.RATE_LIMIT_UPLOADS = 1
    url = address(start(client, 1000))
    for piece in range(3):
        assert send(client, url, piece, b'x').status_code == 204
        offset(client, url)


@pytest.mark.usefixtures('key_gates')
def test_no_partial_upload_starts_while_uploads_are_closed(client):
    # No lawyer holds a group key here, so nothing uploaded could be read.
    assert start(client, 1000).status_code == 503
    assert not PartialUpload.objects.exists()


def test_the_protocol_is_announced_and_its_version_checked(client, settings):
    options = client.options(START_URL)
    assert options.status_code == 204
    assert options['Tus-Version'] == '1.0.0' and options['Tus-Max-Size'] == str(settings.UPLOAD_MAX_BYTES)
    assert set(options['Tus-Extension'].split(',')) == {'creation', 'expiration', 'termination'}

    assert client.post(START_URL, headers={'Upload-Length': '1000'}).status_code == 412
    assert start(client, 1000, **{'Tus-Resumable': '0.2.2'}).status_code == 412
    assert not PartialUpload.objects.exists()
