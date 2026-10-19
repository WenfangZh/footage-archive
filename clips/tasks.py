"""Background tasks, run by the Huey worker: `python manage.py run_huey` (SPEC.md §9, §11).

Huey keeps its queue in a SQLite file of its own (settings.HUEY). A task carries only a
clip's id, never anything read from a file or typed by an uploader.

- Ingest is queued once confirmation has committed. A failed attempt is retried after
  1, 2, 4, 8 and 16 minutes; after the last, or at once if the original is rejected,
  the original is deleted and the clip marked failed.
- When the worker starts, clips still processing are queued again: a worker that was
  killed mid-attempt loses that attempt. A clip queued twice is not ingested twice:
  attempts on one clip never run together, and a clip that is no longer processing is
  left alone.
- Every 15 minutes, originals whose form was never confirmed are deleted (clips.cleanup),
  and a backed-up queue alerts the admin (alerts.checks).
- Every hour, vault files left over from a crash are deleted (clips.vault).
"""

import logging

from huey import crontab
from huey.contrib.djhuey import db_periodic_task, db_task, lock_task, on_startup

from alerts.checks import check_ingest_queue
from audit import limits

from . import cleanup, ingest, vault
from .models import Clip

logger = logging.getLogger(__name__)

RETRIES = 5
FIRST_RETRY_DELAY_SECONDS = 60
RETRY_BACKOFF = 2


@db_task(retries=RETRIES, retry_delay=FIRST_RETRY_DELAY_SECONDS, retry_backoff=RETRY_BACKOFF, context=True)
def ingest_clip(clip_id, task=None):
    with lock_task(f'ingest-{clip_id}'):
        clip = Clip.objects.filter(pk=clip_id, status=Clip.Status.PROCESSING).first()
        if clip is None:
            return
        try:
            ingest.ingest(clip)
        except ingest.Rejected as rejection:
            ingest.give_up(clip, rejection.reason)
        except Exception as failure:
            if task is not None and task.retries:
                logger.warning(
                    'Ingest of clip %s failed (%s); %s retries left.',
                    clip_id, type(failure).__name__, task.retries,
                )
                raise
            ingest.give_up(clip, type(failure).__name__)


@on_startup()
def record_changed_limits():
    # The worker reads the queue's alert limits, and serves no request that would check them.
    limits.record_if_changed()


@on_startup()
def queue_unfinished_ingests():
    for clip_id in Clip.objects.filter(status=Clip.Status.PROCESSING).values_list('pk', flat=True):
        ingest_clip(clip_id)


@db_periodic_task(crontab(minute='*/15'))
def delete_unconfirmed_originals():
    cleanup.delete_unconfirmed_originals()
    check_ingest_queue()


@db_periodic_task(crontab(minute='7'))
def delete_stray_vault_files():
    try:
        deleted = vault.delete_stray_files()
    except vault.TooManyStrays as refusal:
        logger.error('%s vault files have no row; none deleted. Check the database first.', refusal)
        return
    # A count only: file names say nothing useful and are better not logged.
    if deleted:
        logger.warning('Deleted %s vault files left over from a crash.', deleted)
