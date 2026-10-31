"""Deleting originals whose form was never confirmed, and partial uploads never finished (SPEC.md §8).

After UNCONFIRMED_ORIGINAL_MINUTES the original is deleted and nothing is kept, not
even a hash, since no clip ever existed. Originals whose form was confirmed wait for
ingest and are never deleted for age (SPEC.md §9).

A partial upload not finished within PARTIAL_UPLOAD_MINUTES of starting is deleted too.

Files in quarantine without a database row (left by a crash) and stale temporary files
of incoming uploads go too, but only once they are as old as the deadline: a file
being received, or just moved into quarantine, is recent.
"""

import datetime

from django.conf import settings
from django.utils import timezone

from .models import Original, PartialUpload


def delete_unconfirmed_originals(now=None):
    """Delete what has outlived the deadline; returns (originals, stray files) deleted."""
    now = now or timezone.now()
    deadline = now - datetime.timedelta(minutes=settings.UNCONFIRMED_ORIGINAL_MINUTES)

    originals = 0
    for original in Original.objects.filter(clip__isnull=True, received_at__lte=deadline):
        original.file.delete(save=False)
        original.delete()
        originals += 1

    known = {*Original.objects.values_list('file', flat=True), *PartialUpload.objects.values_list('file', flat=True)}
    stray = 0
    for path in settings.QUARANTINE_DIR.rglob('*'):
        if not path.is_file():
            continue
        modified = datetime.datetime.fromtimestamp(path.stat().st_mtime, tz=datetime.UTC)
        if modified > deadline:
            continue
        if path.parent == settings.FILE_UPLOAD_TEMP_DIR or path.name not in known:
            path.unlink(missing_ok=True)
            stray += 1
    return originals, stray


def delete_expired_partial_uploads(now=None):
    """Delete partial uploads whose time is up; returns how many."""
    now = now or timezone.now()
    started_by = now - datetime.timedelta(minutes=settings.PARTIAL_UPLOAD_MINUTES)
    # One at a time: a partial upload finished meanwhile is no longer this one's to delete.
    return sum(partial.discard() for partial in PartialUpload.objects.filter(started_at__lte=started_by))
