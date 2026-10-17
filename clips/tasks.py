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
- Every 15 minutes, originals whose form was never confirmed are deleted (clips.cleanup).
"""

import logging

from huey import crontab
from huey.contrib.djhuey import db_periodic_task, db_task, lock_task, on_startup

from . import cleanup, ingest
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
def queue_unfinished_ingests():
    for clip_id in Clip.objects.filter(status=Clip.Status.PROCESSING).values_list('pk', flat=True):
        ingest_clip(clip_id)


@db_periodic_task(crontab(minute='*/15'))
def delete_unconfirmed_originals():
    cleanup.delete_unconfirmed_originals()
    waiting = Clip.objects.filter(status=Clip.Status.PROCESSING).count()
    # Until alerts exist (M3), the size of the ingest queue goes to the log.
    logger.info('%s clips waiting for ingest.', waiting)
