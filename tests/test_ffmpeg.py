import json
import shutil
import subprocess

from core.checks import check_ffmpeg_available


def test_check_passes_when_ffmpeg_and_ffprobe_are_installed():
    assert check_ffmpeg_available(app_configs=None) == []


def test_check_reports_each_missing_program(monkeypatch):
    real_which = shutil.which
    monkeypatch.setattr(shutil, 'which', lambda name: None if name == 'ffprobe' else real_which(name))
    errors = check_ffmpeg_available(app_configs=None)
    assert [error.id for error in errors] == ['core.E002']
    assert 'ffprobe' in errors[0].msg


def test_check_reports_both_when_neither_is_installed(monkeypatch):
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    errors = check_ffmpeg_available(app_configs=None)
    assert [error.id for error in errors] == ['core.E001', 'core.E002']


def test_synthetic_video_is_a_real_video(synthetic_video):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams',
         str(synthetic_video)],
        check=True, capture_output=True, text=True,
    )
    probe = json.loads(result.stdout)
    assert sorted(stream['codec_type'] for stream in probe['streams']) == ['audio', 'video']
    assert abs(float(probe['format']['duration']) - 1.0) < 0.2
