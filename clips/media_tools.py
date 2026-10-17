"""Running ffprobe and ffmpeg on untrusted files, fenced in (SPEC.md §8, §9, §10).

Both programs have to parse the file, so every run:

- opens only the container formats phones record in. Without this, ffprobe reports a
  JPEG as a one-frame video, and playlist formats such as HLS can make ffmpeg open
  other files or URLs;
- opens only local files, and gets its arguments as a list, never through a shell;
- gets a bare environment, so the archive's keys are not handed to it. It still runs
  as the server's own user, so a flaw in it could read the files that user can (.env,
  the database): only a sandbox would close that;
- is stopped after a time limit.

Nothing the programs print about a file is logged: it may hold what the file says
about its filmer.
"""

import os
import subprocess

# Demuxer names as ffmpeg knows them: MP4 and QuickTime (iPhone, most Android
# phones), and Matroska and WebM (some Android phones).
ALLOWED_FORMATS = ('mov', 'mp4', 'm4a', '3gp', '3g2', 'mj2', 'matroska', 'webm')


class ToolFailed(Exception):
    """ffprobe or ffmpeg did not finish its work. `reason` is a fixed code, safe to log."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def input_options():
    """Options that must come before an untrusted input."""
    return ['-protocol_whitelist', 'file', '-format_whitelist', ','.join(ALLOWED_FORMATS)]


def file_url(path):
    # The file: prefix stops a name such as 'http:x' being read as a protocol.
    return f'file:{os.fspath(path)}'


def run(command, timeout):
    """Run ffprobe or ffmpeg; returns its standard output, or raises ToolFailed."""
    try:
        result = subprocess.run(
            command, capture_output=True, timeout=timeout,
            env={'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LC_ALL': 'C'},
        )
    except subprocess.TimeoutExpired:
        raise ToolFailed('timeout') from None
    if result.returncode != 0:
        if b'not on whitelist' in result.stderr:
            raise ToolFailed('format_not_allowed')
        raise ToolFailed('unreadable')
    return result.stdout
