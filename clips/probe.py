"""The quick read at upload (SPEC.md §8): is it a video, and what time and GPS does it carry?

The file is untrusted, and ffprobe has to parse it, so the run is fenced in:

- only the container formats phones record in are allowed. Without this, ffprobe
  reports a JPEG as a one-frame video, and playlist formats such as HLS can make it
  open other files or URLs;
- only local files can be opened, and the path is passed as an argument list, never
  through a shell;
- ffprobe gets a bare environment, so a flaw in it cannot read the archive's keys;
- the run is stopped after PROBE_TIMEOUT_SECONDS.

Only the metadata time and the GPS position are taken from the file. The GPS position
is precise: it may pre-fill the form and give the metadata location's cell, and must
never be stored itself (ADR 0002). Nothing read from the file is logged.
"""

import datetime
import json
import os
import re
import subprocess
from dataclasses import dataclass

# Demuxer names as ffprobe knows them: MP4 and QuickTime (iPhone, most Android
# phones), and Matroska and WebM (some Android phones).
ALLOWED_FORMATS = ('mov', 'mp4', 'm4a', '3gp', '3g2', 'mj2', 'matroska', 'webm')
PROBE_TIMEOUT_SECONDS = 30

# Times some software writes when it has none: they are not capture times.
NO_TIME_MARKERS = (
    datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC),
    datetime.datetime(1904, 1, 1, tzinfo=datetime.UTC),
)

# ISO 6709 in decimal degrees, as phones write it: "+48.8566+002.3522+035.000/".
ISO_6709 = re.compile(r'^([+-]\d{2}(?:\.\d+)?)([+-]\d{3}(?:\.\d+)?)')


class NotAVideo(Exception):
    """The file is not a video we accept. `reason` is a fixed code, safe to log."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class QuickRead:
    metadata_time: datetime.datetime | None
    # Precise (latitude, longitude). Never store it: see the module docstring.
    gps: tuple[float, float] | None


def quick_read(path):
    """Check that `path` is a video we accept and read its metadata time and GPS position."""
    probe = run_ffprobe(path)
    if not set(probe.get('format', {}).get('format_name', '').split(',')) <= set(ALLOWED_FORMATS):
        raise NotAVideo('format_not_allowed')
    streams = probe.get('streams', [])
    # Cover art in an audio file shows up as a video stream marked as an attached picture.
    if not any(
        stream.get('codec_type') == 'video' and not stream.get('disposition', {}).get('attached_pic')
        for stream in streams
    ):
        raise NotAVideo('no_video_stream')
    if not duration(probe) > 0:
        raise NotAVideo('no_duration')
    tags = probe['format'].get('tags', {})
    video_tags = next((s.get('tags', {}) for s in streams if s.get('codec_type') == 'video'), {})
    return QuickRead(metadata_time=read_time(tags, video_tags), gps=read_gps(tags))


def run_ffprobe(path):
    command = [
        'ffprobe', '-v', 'error', '-hide_banner',
        '-protocol_whitelist', 'file',
        '-format_whitelist', ','.join(ALLOWED_FORMATS),
        '-print_format', 'json', '-show_format', '-show_streams',
        f'file:{os.fspath(path)}',
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, timeout=PROBE_TIMEOUT_SECONDS,
            env={'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LC_ALL': 'C'},
        )
    except subprocess.TimeoutExpired:
        raise NotAVideo('timeout') from None
    if result.returncode != 0:
        if b'not on whitelist' in result.stderr:
            raise NotAVideo('format_not_allowed')
        raise NotAVideo('unreadable')
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise NotAVideo('unreadable') from None


def duration(probe):
    for value in [probe.get('format', {}).get('duration')] + [
        stream.get('duration') for stream in probe.get('streams', [])
    ]:
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0


def read_time(format_tags, video_tags):
    """The capture time as found, preferring Apple's tag, which keeps the UTC offset."""
    for value in (
        format_tags.get('com.apple.quicktime.creationdate'),
        format_tags.get('creation_time'),
        video_tags.get('creation_time'),
    ):
        moment = parse_time(value)
        if moment is not None:
            return moment
    return None


def parse_time(value):
    try:
        moment = datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    # Without a UTC offset the time cannot be placed, so it is not used.
    if moment.tzinfo is None or moment in NO_TIME_MARKERS:
        return None
    return moment.astimezone(datetime.UTC)


def read_gps(format_tags):
    for key in ('com.apple.quicktime.location.ISO6709', 'location', 'location-eng'):
        position = parse_iso_6709(format_tags.get(key))
        if position is not None:
            return position
    return None


def parse_iso_6709(value):
    match = ISO_6709.match(value or '')
    if not match:
        return None
    latitude, longitude = float(match[1]), float(match[2])
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude
