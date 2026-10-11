"""System checks: run by `manage.py check`, `runserver`, `migrate` and the test suite.

ffprobe validates every uploaded file and ffmpeg builds evidence copies and proxies
(SPEC.md §8, §9), so the archive refuses to start without them.
"""

import shutil

from django.core.checks import Error, register

REQUIRED_PROGRAMS = [
    ('ffmpeg', 'core.E001'),
    ('ffprobe', 'core.E002'),
]


@register()
def check_ffmpeg_available(app_configs, **kwargs):
    return [
        Error(
            f'{program} was not found on the PATH.',
            hint='Install it with: sudo apt install ffmpeg',
            id=check_id,
        )
        for program, check_id in REQUIRED_PROGRAMS
        if shutil.which(program) is None
    ]
