import datetime
import os
import uuid

import pytest
from django.core.files import File
from django.core.files.base import ContentFile
from django.urls import reverse
from huey.contrib.djhuey import HUEY

from clips import ingest, probe, tasks, vault
from clips.models import Clip, HashRecord, Original
from tests.media import H264

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def empty_queue():
    HUEY.flush()
    yield
    HUEY.flush()


@pytest.fixture
def video(make_media):
    return make_media('phone.mp4', *H264, '-c:a', 'aac', '-metadata', 'creation_time=2026-05-01T12:03:22Z')


def confirmed_clip(path):
    """A clip as confirmation leaves it: processing, with its original in quarantine."""
    clip = Clip.objects.create(code_hash=uuid.uuid4().hex * 2, metadata_time=probe.quick_read(path).metadata_time)
    original = Original(clip=clip)
    with open(path, 'rb') as source:
        original.file.save('original', File(source))
    return clip


class LastAttempt:
    """What Huey passes as `task` on an attempt with no retries left."""
    retries = 0


def failing_ingest(monkeypatch):
    def broken(clip):
        raise OSError('disk full')
    monkeypatch.setattr(ingest, 'ingest', broken)


def test_confirming_an_upload_queues_its_ingest(client, video, django_capture_on_commit_callbacks):
    with open(video, 'rb') as file:
        step_two = client.post(reverse('clips:upload'), {'file': file})['Location']
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(step_two, {'stated_time': '2026-05-01T14:05'})
    assert response.status_code == 200
    clip = Clip.objects.get()
    assert clip.status == Clip.Status.READY
    assert vault.has_file(clip, vault.PROXY)
    assert not Original.objects.exists()


def test_a_failed_attempt_is_retried_with_growing_delays(video, monkeypatch):
    clip = confirmed_clip(video)
    failing_ingest(monkeypatch)
    tasks.ingest_clip(clip.pk)  # Runs at once in tests, and fails.

    delays = []
    far_future = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) + datetime.timedelta(days=1)
    # Run each retry as if its time had come, until none is left.
    while due := HUEY.read_schedule(far_future):
        [retry] = due
        # The delay grows when a retry is scheduled, ready for the next one.
        delays.append(retry.retry_delay / retry.retry_backoff)
        HUEY.execute(retry, timestamp=far_future)
    assert delays == [60, 120, 240, 480, 960]  # 31 minutes in all.

    # M2 done-when (f): after the last retry, the original is gone and the clip failed.
    clip.refresh_from_db()
    assert clip.status == Clip.Status.FAILED
    assert not Original.objects.exists()


def test_an_attempt_with_retries_left_keeps_the_original(video, monkeypatch):
    clip = confirmed_clip(video)
    failing_ingest(monkeypatch)

    class RetriesLeft:
        retries = 3

    with pytest.raises(OSError):
        tasks.ingest_clip.call_local(clip.pk, task=RetriesLeft())
    clip.refresh_from_db()
    assert clip.status == Clip.Status.PROCESSING
    assert Original.objects.filter(clip=clip).exists()


def test_after_the_last_failed_retry_the_original_is_deleted(
    video, monkeypatch, django_capture_on_commit_callbacks,
):
    # M2 done-when (f).
    clip = confirmed_clip(video)
    original = Original.objects.get(clip=clip)
    failing_ingest(monkeypatch)
    with django_capture_on_commit_callbacks(execute=True):
        tasks.ingest_clip.call_local(clip.pk, task=LastAttempt())
    clip.refresh_from_db()
    assert clip.status == Clip.Status.FAILED
    assert not Original.objects.exists()
    assert not original.file.storage.exists(original.file.name)
    assert not HashRecord.objects.exists()


def test_a_rejected_original_is_not_retried(monkeypatch, video):
    clip = confirmed_clip(video)

    def reject(clip):
        raise ingest.Rejected('no_video_stream')

    monkeypatch.setattr(ingest, 'ingest', reject)
    tasks.ingest_clip(clip.pk)
    assert HUEY.scheduled() == []
    clip.refresh_from_db()
    assert clip.status == Clip.Status.FAILED


def test_a_deleted_or_finished_clip_is_left_alone(video, monkeypatch):
    calls = []
    monkeypatch.setattr(ingest, 'ingest', calls.append)
    tasks.ingest_clip.call_local(uuid.uuid4(), task=LastAttempt())
    clip = confirmed_clip(video)
    Clip.objects.filter(pk=clip.pk).update(status=Clip.Status.READY)
    tasks.ingest_clip.call_local(clip.pk, task=LastAttempt())
    assert calls == []


def test_clips_still_processing_are_queued_again_when_the_worker_starts(video):
    stuck = confirmed_clip(video)
    tasks.queue_unfinished_ingests()
    stuck.refresh_from_db()
    assert stuck.status == Clip.Status.READY


def test_unconfirmed_originals_are_deleted_on_a_schedule():
    old = Original(received_at=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC))
    old.file.save('original', ContentFile(b'synthetic bytes'))
    tasks.delete_unconfirmed_originals.call_local()
    assert not Original.objects.exists()


def test_stray_vault_files_are_deleted_on_a_schedule(settings):
    stray = settings.VAULT_DIR / f'{uuid.uuid4().hex}.mp4'
    stray.write_bytes(b'left over')
    moment = (datetime.datetime.now(datetime.UTC) - 2 * vault.STRAY_FILE_AGE).timestamp()
    os.utime(stray, (moment, moment))
    tasks.delete_stray_vault_files.call_local()
    assert not stray.exists()


def test_the_worker_never_runs_inside_a_request_outside_tests():
    from config import settings as real_settings
    assert real_settings.HUEY['immediate'] is False
