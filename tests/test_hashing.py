import hashlib
import subprocess

import pytest

from clips import hashing
from clips.media_tools import ToolFailed
from tests.media import H264, run_ffmpeg

TIMEOUT = 60


def test_the_file_hash_is_the_sha256_of_the_bytes(tmp_path):
    path = tmp_path / 'file'
    path.write_bytes(b'some bytes' * 100_000)
    assert hashing.file_hash(path) == hashlib.sha256(b'some bytes' * 100_000).hexdigest()


def test_each_video_and_audio_stream_is_hashed_in_order(synthetic_video):
    hashes = hashing.stream_hashes(synthetic_video, TIMEOUT)
    assert [kind for kind, _ in hashes] == ['v', 'a']
    assert all(len(digest) == 64 for _, digest in hashes)


def test_stream_hashes_ignore_the_container_metadata(make_media, tmp_path):
    path = make_media('phone.mp4', *H264, '-c:a', 'aac', '-metadata', 'creation_time=2026-05-01T12:03:22Z')
    retagged = tmp_path / 'retagged.mp4'
    run_ffmpeg(
        '-i', str(path), '-map', '0', '-c', 'copy',
        '-metadata', 'creation_time=2020-01-01T00:00:00Z', '-metadata', 'location=+48.8532+002.3692/',
        str(retagged),
    )
    assert hashing.file_hash(retagged) != hashing.file_hash(path)
    assert hashing.stream_hashes(retagged, TIMEOUT) == hashing.stream_hashes(path, TIMEOUT)


def test_stream_hashes_change_when_the_picture_does(make_media):
    first = make_media('first.mp4', *H264, '-an')
    second = make_media('second.mp4', *H264, '-an', '-vf', 'negate')
    assert hashing.stream_hashes(first, TIMEOUT) != hashing.stream_hashes(second, TIMEOUT)


def test_cover_art_is_not_hashed(make_media, tmp_path):
    cover = make_media('cover.jpg', '-frames:v', '1', '-an')
    video = make_media('video.mp4', *H264, '-c:a', 'aac')
    path = tmp_path / 'with_cover.mp4'
    run_ffmpeg(
        '-i', str(video), '-i', str(cover), '-map', '0', '-map', '1', '-c', 'copy',
        '-disposition:v:1', 'attached_pic', str(path),
    )
    assert [kind for kind, _ in hashing.stream_hashes(path, TIMEOUT)] == ['v', 'a']


def test_a_file_without_video_cannot_be_hashed(make_media):
    with pytest.raises(ToolFailed):
        hashing.stream_hashes(make_media('sound.mp4', '-vn', '-c:a', 'aac'), TIMEOUT)


def test_other_formats_are_not_opened(make_media):
    with pytest.raises(ToolFailed, match='format_not_allowed'):
        hashing.stream_hashes(make_media('video.avi', '-an'), TIMEOUT)


def test_ffmpeg_gets_no_secrets_and_no_shell(monkeypatch, synthetic_video, settings):
    calls = []
    real_run = subprocess.run

    def spy(command, **kwargs):
        calls.append((command, kwargs))
        return real_run(command, **kwargs)

    monkeypatch.setattr(subprocess, 'run', spy)
    monkeypatch.setenv('UPLOAD_CODE_KEY', settings.UPLOAD_CODE_KEY)
    hashing.stream_hashes(synthetic_video, TIMEOUT)
    [(command, kwargs)] = calls
    assert isinstance(command, list) and not kwargs.get('shell')
    assert set(kwargs['env']) == {'PATH', 'LC_ALL'}
    assert kwargs['timeout'] == TIMEOUT
    assert '-protocol_whitelist' in command and '-format_whitelist' in command
