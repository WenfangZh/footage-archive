"""Ingest: from the original in quarantine to the evidence copy, proxy and hash record (SPEC.md §9).

`ingest(clip)` makes one attempt at steps 2 to 9. It raises:

- Rejected when the original is not a video we accept, or does not say what the quick
  read at upload found in it. Trying again would give the same answer, so the caller
  gives up at once (`give_up`).
- any other exception when the attempt failed in a way a later one might not, such as
  a time limit or a full disk. The caller retries, and gives up after the last retry.

An attempt has INGEST_ATTEMPT_MINUTES in all; every run of ffmpeg or ffprobe gets
what is left of it. Working files are made in quarantine, since they come from an
untrusted file, and are removed at the end of the attempt whatever happens. Any left
by a crash are removed by clips.cleanup once they are as old as its deadline (2 hours
by default), which no attempt lasts.

Nothing read from the file is logged. Failures are logged with the clip's id and a
fixed reason only.
"""

import datetime
import logging
import tempfile
import time
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import evidence, grid, hashing, media_tools, probe, proxy, vault
from .models import Clip, HashRecord, Original

logger = logging.getLogger(__name__)


class Rejected(Exception):
    """The original will never ingest. `reason` is a fixed code, safe to log."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class Deadline:
    def __init__(self, minutes):
        self.end = time.monotonic() + minutes * 60

    def remaining(self):
        left = self.end - time.monotonic()
        if left <= 0:
            raise media_tools.ToolFailed('timeout')
        return left


def ingest(clip):
    """One attempt at ingesting `clip`. Does nothing unless the clip is processing."""
    if clip.status != Clip.Status.PROCESSING:
        return
    original = Original.objects.get(clip=clip)
    deadline = Deadline(settings.INGEST_ATTEMPT_MINUTES)
    with tempfile.TemporaryDirectory(dir=settings.QUARANTINE_DIR, prefix='ingest-') as work:
        result = process(clip, Path(original.file.path), Path(work), deadline)
        finish(clip, original, result)


def process(clip, original_path, work, deadline):
    """Steps 2 to 8. Returns what the clip and its hash record need."""
    # Step 2: the full check.
    try:
        described, read = probe.full_read(original_path, deadline.remaining())
    except probe.NotAVideo as refusal:
        if refusal.reason == 'timeout':
            raise media_tools.ToolFailed('timeout') from None
        raise Rejected(refusal.reason) from None

    # Step 3.
    original_sha256 = hashing.file_hash(original_path)
    original_hashed_at = timezone.now()

    # Step 4.
    original_streams = hashing.stream_hashes(original_path, deadline.remaining())

    # Step 5: what the quick read found must still be there. The precise GPS position
    # goes no further than this function.
    confirm_quick_read(clip, read)
    width, height = probe.display_size(described)

    # Step 6.
    container = evidence.container_for(described)
    evidence_path = work / f'evidence.{container.extension}'
    evidence.make_evidence_copy(original_path, evidence_path, container, deadline.remaining())

    # Step 7: the evidence copy holds the very same packets.
    evidence_streams = hashing.stream_hashes(evidence_path, deadline.remaining())
    if evidence_streams != original_streams:
        logger.error('Ingest of clip %s: stream hashes differ after remuxing.', clip.pk)
        raise media_tools.ToolFailed('stream_hashes_differ')

    # Step 8.
    proxy_path = work / f'proxy.{proxy.PROXY_EXTENSION}'
    proxy.make_proxy(evidence_path, proxy_path, deadline.remaining())

    return {
        'evidence_path': evidence_path,
        'extension': container.extension,
        'proxy_path': proxy_path,
        'duration': probe.duration(described),
        'width': width,
        'height': height,
        'hash_record': HashRecord(
            original_sha256=original_sha256,
            original_stream_hashes=[list(pair) for pair in original_streams],
            original_hashed_at=original_hashed_at,
            evidence_sha256=hashing.file_hash(evidence_path),
            evidence_stream_hashes=[list(pair) for pair in evidence_streams],
            evidence_hashed_at=timezone.now(),
        ),
    }


def confirm_quick_read(clip, read):
    if read.metadata_time != clip.metadata_time:
        raise Rejected('metadata_time_differs')
    if (read.gps is None) != (clip.metadata_cell is None):
        raise Rejected('gps_differs')
    if read.gps is not None:
        cell = grid.cell_for_point(*read.gps, grid.resolution(clip.metadata_cell))
        if cell != clip.metadata_cell:
            raise Rejected('gps_differs')


def finish(clip, original, result):
    """Step 9: the files go to the vault, the hash record is kept, the original deleted."""
    # The files are copied before the transaction, not inside it. SQLite locks the whole
    # database while a transaction is open, so copying up to 2 GB inside one would hold
    # up every other write, such as lawyers' audit entries, until they gave up with
    # "database is locked". Postgres locks rows, so there this mainly keeps the
    # transaction short.
    stored = []
    try:
        stored.append(vault.store_file(result['evidence_path'], result['extension']))
        stored.append(vault.store_file(result['proxy_path'], proxy.PROXY_EXTENSION))
        evidence_name, proxy_name = stored
        with transaction.atomic():
            # The code holder may have deleted the clip meanwhile.
            if not Clip.objects.select_for_update().filter(
                pk=clip.pk, status=Clip.Status.PROCESSING,
            ).exists():
                return
            vault.record_file(clip, vault.EVIDENCE_COPY, evidence_name)
            vault.record_file(clip, vault.PROXY, proxy_name)
            record = result['hash_record']
            record.clip = clip
            record.save()
            # Only ingest's own fields: the code holder may have edited others meanwhile.
            Clip.objects.filter(pk=clip.pk).update(
                status=Clip.Status.READY,
                duration=datetime.timedelta(seconds=result['duration']),
                width=result['width'],
                height=result['height'],
            )
            original.delete()
            transaction.on_commit(lambda: original.file.delete(save=False))
    finally:
        # Whatever no row took, after a failure or a clip deleted meanwhile, must not stay.
        vault.delete_unrecorded_files(stored)
    clip.refresh_from_db()


def give_up(clip, reason):
    """After the last failed attempt, or a rejection: delete the original, mark the clip failed.

    The clip stays, with no files, so that a code holder who comes back sees that the
    upload failed and knows to upload again (SPEC.md §9).
    """
    logger.error('Ingest of clip %s failed for good: %s', clip.pk, reason)
    with transaction.atomic():
        if not Clip.objects.select_for_update().filter(pk=clip.pk, status=Clip.Status.PROCESSING).exists():
            return
        vault.delete_all(clip)
        Clip.objects.filter(pk=clip.pk).update(status=Clip.Status.FAILED)
        original = Original.objects.filter(clip=clip).first()
        if original is not None:
            original.delete()
            transaction.on_commit(lambda: original.file.delete(save=False))
        clip.status = Clip.Status.FAILED
