"""Making the proxy: a low-resolution H.264 version for previews (SPEC.md §9, step 8).

The proxy is made from the evidence copy, which has no metadata left, and is never
evidence itself. It keeps the first video stream and the first audio stream, turned
upright (decoding applies a portrait video's rotation), shrunk to fit within
PROXY_MAX_SIDE pixels and never enlarged, in an MP4 file that browsers can play.
Like the evidence copy, it carries no metadata, and the muxer adds no time or
identifiers of its own.
"""

from pathlib import Path

from . import media_tools

PROXY_MAX_SIDE = 640
PROXY_EXTENSION = 'mp4'


def make_proxy(evidence_path, destination_path, timeout):
    """Write the proxy of `evidence_path` to `destination_path`, which must not exist."""
    # ffmpeg refuses to overwrite with -n, but still exits as if it had succeeded.
    if Path(destination_path).exists():
        raise FileExistsError(destination_path)
    side = PROXY_MAX_SIDE
    media_tools.run([
        'ffmpeg', '-nostdin', '-n', '-v', 'error', '-hide_banner',
        *media_tools.input_options(), '-i', media_tools.file_url(evidence_path),
        '-map', '0:V:0', '-map', '0:a:0?',
        '-vf', (
            f"scale='min({side},iw)':'min({side},ih)'"
            ':force_original_aspect_ratio=decrease:force_divisible_by=2'
        ),
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '28', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '96k',
        '-movflags', '+faststart',
        '-map_metadata', '-1', '-map_chapters', '-1', '-fflags', '+bitexact',
        # Encoding adds a tag naming the encoder; an empty value removes it. Older
        # ffmpeg (6.1) adds it back, naming only the encoder, without its version.
        '-metadata:s', 'encoder=',
        '-f', 'mp4', media_tools.file_url(destination_path),
    ], timeout)
