import io

import pytest
from django.test import RequestFactory

from clips import streaming

CONTENT = bytes(range(256)) * 10  # 2560 bytes, so that every byte is told apart.
SIZE = len(CONTENT)


def serve(range_header=None, content=CONTENT):
    headers = {'HTTP_RANGE': range_header} if range_header is not None else {}
    file = io.BytesIO(content)
    response = streaming.file_response(RequestFactory().get('/', **headers), file, len(content), 'video/mp4')
    return response, file


def body(response):
    return b''.join(response.streaming_content) if response.streaming else response.content


def test_a_request_without_a_range_gets_the_whole_file():
    response, _ = serve()
    assert response.status_code == 200
    assert body(response) == CONTENT
    assert response['Content-Length'] == str(SIZE)
    assert response['Content-Type'] == 'video/mp4'
    assert 'Content-Range' not in response


def test_a_start_and_end_range_gets_that_slice():
    response, _ = serve('bytes=10-19')
    assert response.status_code == 206
    assert body(response) == CONTENT[10:20]
    assert response['Content-Range'] == f'bytes 10-19/{SIZE}'
    assert response['Content-Length'] == '10'


def test_an_open_ended_range_runs_to_the_end():
    response, _ = serve('bytes=2000-')
    assert response.status_code == 206
    assert body(response) == CONTENT[2000:]
    assert response['Content-Range'] == f'bytes 2000-{SIZE - 1}/{SIZE}'
    assert response['Content-Length'] == str(SIZE - 2000)


def test_the_unit_is_read_in_any_case():
    response, _ = serve('Bytes=0-9')
    assert response.status_code == 206


def test_a_suffix_range_gets_the_last_bytes():
    response, _ = serve('bytes=-100')
    assert response.status_code == 206
    assert body(response) == CONTENT[-100:]
    assert response['Content-Range'] == f'bytes {SIZE - 100}-{SIZE - 1}/{SIZE}'


def test_a_suffix_longer_than_the_file_gets_the_whole_file():
    response, _ = serve(f'bytes=-{SIZE + 50}')
    assert response.status_code == 206
    assert body(response) == CONTENT
    assert response['Content-Range'] == f'bytes 0-{SIZE - 1}/{SIZE}'


def test_a_range_past_the_end_is_clamped():
    response, _ = serve(f'bytes=2500-{SIZE + 1000}')
    assert response.status_code == 206
    assert body(response) == CONTENT[2500:]
    assert response['Content-Range'] == f'bytes 2500-{SIZE - 1}/{SIZE}'
    assert response['Content-Length'] == str(SIZE - 2500)


def test_a_large_slice_is_streamed_in_chunks(monkeypatch):
    monkeypatch.setattr(streaming, 'CHUNK_SIZE', 100)
    response, _ = serve('bytes=50-549')
    chunks = list(response.streaming_content)
    assert [len(chunk) for chunk in chunks] == [100] * 5
    assert b''.join(chunks) == CONTENT[50:550]


@pytest.mark.parametrize('header', [f'bytes={SIZE}-', f'bytes={SIZE + 5}-{SIZE + 9}', 'bytes=-0'])
def test_an_unsatisfiable_range_gets_416(header):
    response, file = serve(header)
    assert response.status_code == 416
    assert response['Content-Range'] == f'bytes */{SIZE}'
    assert response['Accept-Ranges'] == 'bytes'
    assert file.closed


def test_any_range_of_an_empty_file_gets_416():
    response, _ = serve('bytes=0-', content=b'')
    assert response.status_code == 416
    assert response['Content-Range'] == 'bytes */0'


@pytest.mark.parametrize(
    'header', [
        'bytes=0-9,20-29', 'bytes=-', 'bytes=20-10', 'bytes=a-b', 'items=0-9', 'bytes 0-9', '',
        # Too long for int() to read, and for any file.
        'bytes=0-' + '9' * 5000,
    ],
)
def test_a_malformed_or_multiple_range_is_ignored(header):
    response, _ = serve(header)
    assert response.status_code == 200
    assert body(response) == CONTENT
    assert 'Content-Range' not in response


def test_every_response_has_the_safety_headers():
    for header in (None, 'bytes=0-9', f'bytes={SIZE}-'):
        response, _ = serve(header)
        assert response['Accept-Ranges'] == 'bytes'
        assert response['X-Content-Type-Options'] == 'nosniff'
        assert response['Cache-Control'] == 'no-store, private'


@pytest.mark.django_db  # Closing a response sends request_finished, which checks the connections.
def test_the_file_is_closed_when_the_response_is_closed():
    response, file = serve('bytes=0-9')
    body(response)
    assert not file.closed
    response.close()
    assert file.closed


@pytest.mark.django_db
def test_the_file_is_closed_even_if_the_body_was_never_read():
    response, file = serve()
    response.close()
    assert file.closed
