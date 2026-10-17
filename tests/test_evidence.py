import json
import re
import subprocess

import pytest

from clips import evidence, hashing
from clips.media_tools import ToolFailed
from clips.probe import run_ffprobe
from tests.media import H264, run_ffmpeg

TIMEOUT = 60
# Fake values injected into synthetic videos: public places and made-up devices.
FAKE_GPS = '+48.8532+002.3692+035.000/'
FAKE_TAGS = {
    'com.apple.quicktime.location.ISO6709': FAKE_GPS,
    'com.apple.quicktime.make': 'FakePhoneMaker',
    'com.apple.quicktime.model': 'FakePhone 12',
    'com.apple.quicktime.software': 'FakeOS 9.9',
    'com.apple.quicktime.creationdate': '2026-05-01T14:03:22+0200',
    'location': FAKE_GPS,
    'creation_time': '2026-05-01T12:03:22Z',
    'encoder': 'FakeEncoder',
}


def tag_options(tags):
    return [option for key, value in tags.items() for option in ('-metadata', f'{key}={value}')]


@pytest.fixture
def phone_mov(make_media):
    """A QuickTime file like an iPhone's: tagged time, GPS and device, and a timecode track."""
    return make_media(
        'phone.mov', *H264, '-c:a', 'aac', '-movflags', 'use_metadata_tags',
        '-timecode', '01:00:00:00', *tag_options(FAKE_TAGS),
        '-metadata:s:v', 'handler_name=FakeCamera', '-metadata:s:a', 'title=FakeMic',
    )


def copy_of(path, tmp_path):
    container = evidence.container_for(run_ffprobe(path))
    destination = tmp_path / f'evidence.{container.extension}'
    evidence.make_evidence_copy(path, destination, container, TIMEOUT)
    return destination


def full_probe(path):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams',
         '-show_chapters', str(path)],
        capture_output=True, check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize('name, options', [
    ('phone.mov', [*H264, '-c:a', 'aac', '-metadata', f'location={FAKE_GPS}']),
    ('phone.mp4', [*H264, '-c:a', 'aac', '-metadata', f'location={FAKE_GPS}']),
    ('phone.mkv', [*H264, '-c:a', 'aac', '-metadata', f'location={FAKE_GPS}']),
    ('phone.webm', ['-c:v', 'libvpx-vp9', '-c:a', 'libopus', '-metadata', f'location={FAKE_GPS}']),
])
def test_stream_hashes_match_before_and_after(make_media, tmp_path, name, options):
    # M2 done-when (a).
    original = make_media(name, *options)
    copy = copy_of(original, tmp_path)
    assert hashing.stream_hashes(copy, TIMEOUT) == hashing.stream_hashes(original, TIMEOUT)
    assert hashing.file_hash(copy) != hashing.file_hash(original)


@pytest.mark.parametrize('name, muxer', [
    ('phone.mov', 'mov'), ('phone.mp4', 'mp4'), ('phone.mkv', 'matroska'), ('phone.webm', 'matroska'),
])
def test_the_container_stays_in_the_family(make_media, name, muxer):
    codecs = ['-c:v', 'libvpx-vp9', '-c:a', 'libopus'] if name.endswith('webm') else [*H264, '-c:a', 'aac']
    assert evidence.container_for(run_ffprobe(make_media(name, *codecs))).muxer == muxer


def test_only_video_and_audio_streams_are_kept(phone_mov, tmp_path):
    # M2 done-when (b), the streams.
    assert {s['codec_type'] for s in full_probe(phone_mov)['streams']} == {'video', 'audio', 'data'}
    copy = copy_of(phone_mov, tmp_path)
    assert [s['codec_type'] for s in full_probe(copy)['streams']] == ['video', 'audio']


def test_no_gps_or_device_metadata_is_kept(phone_mov, tmp_path):
    # M2 done-when (b), the metadata: neither as tags nor anywhere in the bytes.
    copy = copy_of(phone_mov, tmp_path)
    described = full_probe(copy)
    assert set(described['format'].get('tags', {})) <= {'major_brand', 'minor_version', 'compatible_brands'}
    for stream in described['streams']:
        assert set(stream.get('tags', {})) <= {'handler_name', 'vendor_id', 'language'}
    assert described.get('chapters', []) == []
    data = copy.read_bytes()
    for value in [*FAKE_TAGS.values(), 'FakeCamera', 'FakeMic', '48.8532']:
        assert value.encode() not in data, value
    assert not re.search(rb'Lavf\d', data)


def test_matroska_gets_no_date_or_metadata(make_media, tmp_path):
    original = make_media('phone.mkv', *H264, '-c:a', 'aac', *tag_options(FAKE_TAGS))
    copy = copy_of(original, tmp_path)
    described = full_probe(copy)
    assert 'creation_time' not in described['format'].get('tags', {})
    data = copy.read_bytes()
    for value in [*FAKE_TAGS.values(), '48.8532']:
        assert value.encode() not in data, value
    # Matroska requires the name of the muxing program; bitexact leaves out its version.
    assert not re.search(rb'Lavf\d', data)


def test_cover_art_is_dropped(make_media, tmp_path):
    cover = make_media('cover.jpg', '-frames:v', '1', '-an')
    video = make_media('video.mp4', *H264, '-c:a', 'aac')
    original = tmp_path / 'with_cover.mp4'
    run_ffmpeg(
        '-i', str(video), '-i', str(cover), '-map', '0', '-map', '1', '-c', 'copy',
        '-disposition:v:1', 'attached_pic', str(original),
    )
    copy = copy_of(original, tmp_path)
    assert [s['codec_type'] for s in full_probe(copy)['streams']] == ['video', 'audio']


def test_a_portrait_video_still_displays_upright(make_media, tmp_path):
    # M2 done-when (c): phones film portrait as landscape frames plus a rotation to apply.
    landscape = make_media('landscape.mp4', *H264, '-c:a', 'aac')
    portrait = tmp_path / 'portrait.mp4'
    run_ffmpeg('-display_rotation', '90', '-i', str(landscape), '-map', '0', '-c', 'copy', str(portrait))
    copy = copy_of(portrait, tmp_path)

    def rotation(path):
        video = next(s for s in full_probe(path)['streams'] if s['codec_type'] == 'video')
        return [d['rotation'] for d in video.get('side_data_list', []) if 'rotation' in d]

    assert rotation(portrait) == [90]
    assert rotation(copy) == rotation(portrait)
    # Played back, the picture comes out taller than wide.
    frame = tmp_path / 'frame.png'
    run_ffmpeg('-i', str(copy), '-frames:v', '1', str(frame))
    [stream] = full_probe(frame)['streams']
    assert stream['height'] > stream['width']


def test_an_existing_file_is_never_overwritten(synthetic_video, tmp_path):
    destination = tmp_path / 'evidence.mp4'
    destination.write_bytes(b'already here')
    with pytest.raises(FileExistsError):
        evidence.make_evidence_copy(synthetic_video, destination, evidence.MP4, TIMEOUT)
    assert destination.read_bytes() == b'already here'


def test_other_formats_get_no_container():
    with pytest.raises(ToolFailed, match='format_not_allowed'):
        evidence.container_for({'format': {'format_name': 'avi'}})
