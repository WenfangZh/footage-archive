"""Making the evidence copy: the original's streams in a new container (SPEC.md §9, step 6).

The video and audio streams are copied packet for packet, never decoded or
re-encoded, into a new container of the same family as the original's: QuickTime or
MP4 stays QuickTime or MP4, Matroska or WebM becomes Matroska. Staying in the family
means the packets need no repackaging, so their stream hashes stay equal, and every
codec that fitted the original's container fits the copy's.

Dropped: cover art, data tracks (timecodes, a phone's own GPS or sensor tracks),
subtitles, chapters, and all container and stream metadata (times, GPS, device make
and model, software). The muxer runs in bitexact mode, so it adds no time of its own
(which would be the upload time), no random identifiers and no ffmpeg version.
What the player needs from inside the streams survives, such as the rotation of a
portrait video, and so does anything else a phone wrote inside the streams.
"""

from dataclasses import dataclass
from pathlib import Path

from . import media_tools

# The muxer and file extension for each family, by the demuxer name ffprobe reports.
QUICKTIME_FAMILY = 'mov,mp4,m4a,3gp,3g2,mj2'
MATROSKA_FAMILY = 'matroska,webm'


@dataclass(frozen=True)
class Container:
    muxer: str
    extension: str


MP4 = Container('mp4', 'mp4')
QUICKTIME = Container('mov', 'mov')
MATROSKA = Container('matroska', 'mkv')


def container_for(probe):
    """The evidence copy's container, given ffprobe's description of the original."""
    format_info = probe.get('format', {})
    family = format_info.get('format_name')
    if family == MATROSKA_FAMILY:
        return MATROSKA
    if family == QUICKTIME_FAMILY:
        # An iPhone's .mov says so in its brand; other phones write MP4 brands.
        if format_info.get('tags', {}).get('major_brand', '').strip() == 'qt':
            return QUICKTIME
        return MP4
    raise media_tools.ToolFailed('format_not_allowed')


def make_evidence_copy(original_path, destination_path, container, timeout):
    """Write the evidence copy of `original_path` to `destination_path`, which must not exist."""
    # ffmpeg refuses to overwrite with -n, but still exits as if it had succeeded.
    if Path(destination_path).exists():
        raise FileExistsError(destination_path)
    media_tools.run([
        'ffmpeg', '-nostdin', '-n', '-v', 'error', '-hide_banner',
        *media_tools.input_options(), '-i', media_tools.file_url(original_path),
        # V, not v: a video stream that is really cover art is left out.
        '-map', '0:V', '-map', '0:a?', '-c', 'copy',
        '-map_metadata', '-1', '-map_chapters', '-1', '-fflags', '+bitexact',
        '-f', container.muxer, media_tools.file_url(destination_path),
    ], timeout)
