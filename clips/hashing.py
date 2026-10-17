"""The fingerprints that make up a clip's hash record (SPEC.md §9, steps 3, 4 and 7).

- The file hash is the SHA-256 of a file exactly as stored. It lets a copy of the
  original (for example, still on the filmer's phone) be matched against what we hold.
- The stream hashes are the SHA-256 of each kept stream's encoded packets, read without
  decoding them. Remuxing puts the same packets in a new container, so the original
  and its evidence copy have the same stream hashes: this shows the frames and sound
  were not altered. They cover the packets' content, not their timing.

Only video (not cover art) and audio streams are hashed, in file order: the same
streams the evidence copy keeps.
"""

import hashlib
import re

from . import media_tools

STREAM_HASH_LINE = re.compile(r'^(\d+),([va]),SHA256=([0-9a-f]{64})$')


def file_hash(path):
    with open(path, 'rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def stream_hashes(path, timeout):
    """[(kind, hex digest)] for each kept stream, in order; kind is 'v' or 'a'."""
    output = media_tools.run([
        'ffmpeg', '-nostdin', '-v', 'error', '-hide_banner',
        *media_tools.input_options(), '-i', media_tools.file_url(path),
        # V, not v: a video stream that is really cover art is left out.
        '-map', '0:V', '-map', '0:a?', '-c', 'copy',
        '-f', 'streamhash', '-hash', 'sha256', '-',
    ], timeout)
    hashes = []
    for line in output.decode('ascii', errors='replace').splitlines():
        match = STREAM_HASH_LINE.match(line)
        if match is None:
            raise media_tools.ToolFailed('unexpected_hash_output')
        hashes.append((match[2], match[3]))
    if not any(kind == 'v' for kind, _ in hashes):
        raise media_tools.ToolFailed('no_video_stream')
    return hashes
