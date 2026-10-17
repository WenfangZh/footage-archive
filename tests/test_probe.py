import datetime
import subprocess

import pytest

from clips import probe
from clips.probe import NotAVideo, quick_read
from tests.media import H264, run_ffmpeg

UTC = datetime.UTC
# Fake positions of public places, injected into synthetic videos.
BASTILLE = '+48.8532+002.3692/'
BASTILLE_WITH_ALTITUDE = '+48.8532+002.3692+035.000/'


def test_mp4_time_and_gps_are_read(make_media):
    path = make_media(
        'phone.mp4', *H264, '-c:a', 'aac',
        '-metadata', 'creation_time=2026-05-01T12:03:22Z', '-metadata', f'location={BASTILLE}',
    )
    read = quick_read(path)
    assert read.metadata_time == datetime.datetime(2026, 5, 1, 12, 3, 22, tzinfo=UTC)
    assert read.gps == (48.8532, 2.3692)


def test_apple_tags_are_preferred(make_media):
    # iPhones write the local time with its offset; creation_time is often rounded or rewritten.
    path = make_media(
        'phone.mov', *H264, '-c:a', 'aac', '-movflags', 'use_metadata_tags',
        '-metadata', 'com.apple.quicktime.creationdate=2026-05-01T14:03:22+0200',
        '-metadata', f'com.apple.quicktime.location.ISO6709={BASTILLE_WITH_ALTITUDE}',
        '-metadata', 'creation_time=2026-05-01T12:03:20Z',
    )
    read = quick_read(path)
    assert read.metadata_time == datetime.datetime(2026, 5, 1, 12, 3, 22, tzinfo=UTC)
    assert read.gps == (48.8532, 2.3692)


def test_a_video_without_time_or_gps_is_still_accepted(make_media):
    read = quick_read(make_media('plain.mp4', *H264, '-an'))
    assert read == probe.QuickRead(metadata_time=None, gps=None)


def test_a_placeholder_time_is_not_a_capture_time(make_media):
    path = make_media('reset.mp4', *H264, '-an', '-metadata', 'creation_time=1970-01-01T00:00:00Z')
    assert quick_read(path).metadata_time is None


def test_matroska_is_accepted(make_media):
    assert quick_read(make_media('phone.mkv', *H264, '-c:a', 'aac')).metadata_time is None


@pytest.mark.parametrize('name, options, reason', [
    # Without the format allowlist, ffprobe reports pictures as one-frame videos.
    ('still.jpg', ['-frames:v', '1', '-an'], 'format_not_allowed'),
    ('still.png', ['-frames:v', '1', '-an'], 'format_not_allowed'),
    ('animation.gif', ['-an'], 'format_not_allowed'),
    ('sound.wav', ['-vn'], 'format_not_allowed'),
    ('video.avi', ['-an'], 'format_not_allowed'),
    ('sound.mp4', ['-vn', '-c:a', 'aac'], 'no_video_stream'),
])
def test_other_media_is_refused(make_media, name, options, reason):
    with pytest.raises(NotAVideo, match=reason):
        quick_read(make_media(name, *options))


def test_audio_with_cover_art_is_refused(make_media, tmp_path):
    cover = make_media('cover.jpg', '-frames:v', '1', '-an')
    path = tmp_path / 'song.m4a'
    run_ffmpeg(
        '-f', 'lavfi', '-i', 'sine=duration=1', '-i', str(cover), '-map', '0', '-map', '1',
        '-c:a', 'aac', '-c:v', 'copy', '-disposition:v', 'attached_pic', str(path),
    )
    with pytest.raises(NotAVideo, match='no_video_stream'):
        quick_read(path)


@pytest.mark.parametrize('content', [b'', b'hello, not a video', b'\x00' * 4096, b'#!/bin/sh\necho hi\n'])
def test_files_that_are_not_media_are_refused(tmp_path, content):
    path = tmp_path / 'upload'
    path.write_bytes(content)
    with pytest.raises(NotAVideo):
        quick_read(path)


def test_a_cut_off_video_is_refused(make_media):
    path = make_media('whole.mp4', *H264, '-an')
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])
    with pytest.raises(NotAVideo):
        quick_read(path)


def test_a_playlist_pointing_at_another_file_is_refused(synthetic_video, tmp_path):
    # Named .m3u8 so that ffprobe would open it as a playlist, and read the file it
    # points to, if the format allowlist did not stop it.
    playlist = tmp_path / 'upload.m3u8'
    playlist.write_text(
        f'#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\n{synthetic_video}\n#EXT-X-ENDLIST\n'
    )
    with pytest.raises(NotAVideo, match='format_not_allowed'):
        quick_read(playlist)


def test_ffprobe_gets_no_secrets_and_no_shell(monkeypatch, synthetic_video, settings):
    calls = []
    real_run = subprocess.run

    def spy(command, **kwargs):
        calls.append((command, kwargs))
        return real_run(command, **kwargs)

    monkeypatch.setattr(subprocess, 'run', spy)
    monkeypatch.setenv('UPLOAD_CODE_KEY', settings.UPLOAD_CODE_KEY)
    quick_read(synthetic_video)
    [(command, kwargs)] = calls
    assert isinstance(command, list) and not kwargs.get('shell')
    assert set(kwargs['env']) == {'PATH', 'LC_ALL'}
    assert kwargs['timeout'] == probe.PROBE_TIMEOUT_SECONDS


def test_a_hanging_ffprobe_is_stopped(monkeypatch, synthetic_video):
    def hang(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])

    monkeypatch.setattr(subprocess, 'run', hang)
    with pytest.raises(NotAVideo, match='timeout'):
        quick_read(synthetic_video)


@pytest.mark.parametrize('value, expected', [
    ('+48.8532+002.3692/', (48.8532, 2.3692)),
    ('-33.8568+151.2153+005.000/', (-33.8568, 151.2153)),
    ('+48+002/', (48.0, 2.0)),
    ('+91.0000+002.0000/', None),
    ('+48.8532+181.0000/', None),
    ('48.8532,2.3692', None),
    ('', None),
    (None, None),
])
def test_parse_iso_6709(value, expected):
    assert probe.parse_iso_6709(value) == expected


@pytest.mark.parametrize('value, expected', [
    ('2026-05-01T14:03:22+0200', datetime.datetime(2026, 5, 1, 12, 3, 22, tzinfo=UTC)),
    ('2026-05-01T12:03:22.000000Z', datetime.datetime(2026, 5, 1, 12, 3, 22, tzinfo=UTC)),
    ('2026-05-01T12:03:22', None),
    ('1904-01-01T00:00:00Z', None),
    ('yesterday', None),
    (None, None),
])
def test_parse_time(value, expected):
    assert probe.parse_time(value) == expected


def test_the_full_read_counts_the_packets(synthetic_video):
    described, read = probe.full_read(synthetic_video, 60)
    video = next(s for s in described['streams'] if s['codec_type'] == 'video')
    assert int(video['nb_read_packets']) == 10
    assert read == quick_read(synthetic_video)


def test_the_full_read_refuses_what_the_quick_read_refuses(make_media):
    with pytest.raises(NotAVideo, match='format_not_allowed'):
        probe.full_read(make_media('video.avi', '-an'), 60)


@pytest.mark.parametrize('rotation, size', [(None, (160, 120)), ('90', (120, 160)), ('180', (160, 120))])
def test_the_display_size_follows_the_rotation(make_media, tmp_path, rotation, size):
    path = make_media('video.mp4', *H264, '-an')
    if rotation:
        rotated = tmp_path / 'rotated.mp4'
        run_ffmpeg('-display_rotation', rotation, '-i', str(path), '-c', 'copy', str(rotated))
        path = rotated
    assert probe.display_size(probe.run_ffprobe(path)) == size
