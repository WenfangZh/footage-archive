"""The quick read at upload (SPEC.md §8): is it a video, and what time and GPS does it carry?

ffprobe runs fenced in (clips.media_tools): only the formats phones record in, only
local files, a bare environment and a time limit.

Only the metadata time and the GPS position are taken from the file. The GPS position
is precise: it may pre-fill the form and give the metadata location's cell, and must
never be stored itself (ADR 0002). Nothing read from the file is logged.
"""

import datetime
import json
import re
from dataclasses import dataclass

from . import media_tools
from .media_tools import ALLOWED_FORMATS

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
    return check_and_read(run_ffprobe(path))


def full_read(path, timeout):
    """The ingest's check (SPEC.md §9, steps 2 and 5): the quick read's, after demuxing
    every packet of the file without decoding it. Returns ffprobe's description too.
    """
    probe = run_ffprobe(path, timeout, extra_options=['-count_packets'])
    read = check_and_read(probe)
    if not any(
        is_video(stream) and int(stream.get('nb_read_packets') or 0) > 0
        for stream in probe.get('streams', [])
    ):
        raise NotAVideo('no_video_packets')
    return probe, read


def check_and_read(probe):
    if not set(probe.get('format', {}).get('format_name', '').split(',')) <= set(ALLOWED_FORMATS):
        raise NotAVideo('format_not_allowed')
    streams = probe.get('streams', [])
    if not any(is_video(stream) for stream in streams):
        raise NotAVideo('no_video_stream')
    if not duration(probe) > 0:
        raise NotAVideo('no_duration')
    tags = probe['format'].get('tags', {})
    video_tags = next((s.get('tags', {}) for s in streams if s.get('codec_type') == 'video'), {})
    return QuickRead(metadata_time=read_time(tags, video_tags), gps=read_gps(tags))


def is_video(stream):
    # Cover art in an audio file shows up as a video stream marked as an attached picture.
    return stream.get('codec_type') == 'video' and not stream.get('disposition', {}).get('attached_pic')


def run_ffprobe(path, timeout=PROBE_TIMEOUT_SECONDS, extra_options=()):
    command = [
        'ffprobe', '-v', 'error', '-hide_banner', *media_tools.input_options(),
        '-print_format', 'json', '-show_format', '-show_streams', *extra_options,
        media_tools.file_url(path),
    ]
    try:
        output = media_tools.run(command, timeout)
    except media_tools.ToolFailed as failure:
        raise NotAVideo(failure.reason) from None
    try:
        return json.loads(output)
    except ValueError:
        raise NotAVideo('unreadable') from None


def duration(probe):
    """The duration in seconds, or 0 if the file gives none."""
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


def display_size(probe):
    """(width, height) of the first video stream as it displays, after any rotation."""
    video = next(stream for stream in probe['streams'] if is_video(stream))
    width, height = video['width'], video['height']
    rotation = next(
        (data['rotation'] for data in video.get('side_data_list', []) if 'rotation' in data), 0,
    )
    if abs(int(rotation)) % 180 == 90:
        return height, width
    return width, height
