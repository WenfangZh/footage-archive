import json
import re
import subprocess

import pytest

from clips import proxy
from tests.media import H264, TEST_SOUND, run_ffmpeg

TIMEOUT = 60


def describe(path):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', str(path)],
        capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def video_stream(described):
    return next(s for s in described['streams'] if s['codec_type'] == 'video')


@pytest.fixture
def hd_video(tmp_path):
    path = tmp_path / 'hd.mp4'
    run_ffmpeg(
        '-f', 'lavfi', '-i', 'testsrc=duration=1:size=1280x720:rate=10', *TEST_SOUND,
        *H264, '-c:a', 'aac', '-shortest', str(path),
    )
    return path


def test_the_proxy_is_low_resolution_h264(hd_video, tmp_path):
    destination = tmp_path / 'proxy.mp4'
    proxy.make_proxy(hd_video, destination, TIMEOUT)
    described = describe(destination)
    video = video_stream(described)
    assert (video['codec_name'], video['width'], video['height']) == ('h264', 640, 360)
    assert [s['codec_type'] for s in described['streams']] == ['video', 'audio']
    assert destination.stat().st_size < hd_video.stat().st_size


def test_a_small_video_is_not_enlarged(synthetic_video, tmp_path):
    destination = tmp_path / 'proxy.mp4'
    proxy.make_proxy(synthetic_video, destination, TIMEOUT)
    video = video_stream(describe(destination))
    assert (video['width'], video['height']) == (320, 240)


def test_a_video_without_sound_gets_a_silent_proxy(make_media, tmp_path):
    destination = tmp_path / 'proxy.mp4'
    proxy.make_proxy(make_media('silent.mp4', *H264, '-an'), destination, TIMEOUT)
    assert [s['codec_type'] for s in describe(destination)['streams']] == ['video']


def test_a_portrait_video_is_turned_upright(hd_video, tmp_path):
    portrait = tmp_path / 'portrait.mp4'
    run_ffmpeg('-display_rotation', '90', '-i', str(hd_video), '-map', '0', '-c', 'copy', str(portrait))
    destination = tmp_path / 'proxy.mp4'
    proxy.make_proxy(portrait, destination, TIMEOUT)
    video = video_stream(describe(destination))
    assert (video['width'], video['height']) == (360, 640)
    assert not any('rotation' in data for data in video.get('side_data_list', []))


def test_the_proxy_carries_no_metadata(synthetic_video, tmp_path):
    destination = tmp_path / 'proxy.mp4'
    proxy.make_proxy(synthetic_video, destination, TIMEOUT)
    described = describe(destination)
    assert set(described['format'].get('tags', {})) <= {'major_brand', 'minor_version', 'compatible_brands'}
    for stream in described['streams']:
        tags = stream.get('tags', {})
        # Older ffmpeg always names the encoder; bitexact leaves out its version.
        assert re.fullmatch(r'Lavc [a-z0-9_]+', tags.pop('encoder', 'Lavc none'))
        assert set(tags) <= {'handler_name', 'vendor_id', 'language'}


def test_an_existing_file_is_never_overwritten(synthetic_video, tmp_path):
    destination = tmp_path / 'proxy.mp4'
    destination.write_bytes(b'already here')
    with pytest.raises(FileExistsError):
        proxy.make_proxy(synthetic_video, destination, TIMEOUT)
    assert destination.read_bytes() == b'already here'
