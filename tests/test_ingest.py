import datetime
import json
import uuid

import pytest
from django.core.files import File
from django.core.files.base import ContentFile
from django.db import connection

from clips import grid, hashing, ingest, probe, proxy, vault
from clips.editing import delete_clip
from clips.models import Clip, HashRecord, Original
from tests.conftest import open_data_key
from tests.media import H264, run_ffmpeg

# Ingest seals each file's data key to the group keys in use: one exists in each test here.
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('group_key_pair')]

UTC = datetime.UTC
# A fake position of a public place, injected into synthetic videos.
FAKE_GPS = '+48.8532+002.3692+035.000/'


@pytest.fixture
def phone_video(make_media):
    return make_media(
        'phone.mov', *H264, '-c:a', 'aac', '-movflags', 'use_metadata_tags',
        '-metadata', 'com.apple.quicktime.creationdate=2026-05-01T14:03:22+0200',
        '-metadata', f'com.apple.quicktime.location.ISO6709={FAKE_GPS}',
        '-metadata', 'com.apple.quicktime.make=FakePhoneMaker',
    )


def confirmed_clip(path, metadata_resolution=11):
    """A clip as confirmation leaves it: processing, with its original in quarantine."""
    read = probe.quick_read(path)
    clip = Clip(code_hash=uuid.uuid4().hex * 2, metadata_time=read.metadata_time)
    if read.gps is not None:
        clip.set_metadata_location(*read.gps, metadata_resolution)
    clip.save()
    original = Original(clip=clip)
    with open(path, 'rb') as source:
        original.file.save('original', File(source))
    return clip


def test_a_clip_is_ingested(phone_video, django_capture_on_commit_callbacks):
    clip = confirmed_clip(phone_video)
    original_path = Original.objects.get(clip=clip).file.path
    with django_capture_on_commit_callbacks(execute=True):
        ingest.ingest(clip)

    clip.refresh_from_db()
    assert clip.status == Clip.Status.READY
    assert (clip.width, clip.height) == (160, 120)
    assert clip.duration == datetime.timedelta(seconds=1)

    record = HashRecord.objects.get(clip=clip)
    assert record.original_sha256 == hashing.file_hash(phone_video)
    assert record.evidence_stream_hashes == record.original_stream_hashes
    assert [kind for kind, _ in record.original_stream_hashes] == ['v', 'a']

    # M2 done-when (e): the proxy exists and the original is gone.
    assert vault.has_file(clip, vault.EVIDENCE_COPY) and vault.has_file(clip, vault.PROXY)
    assert not Original.objects.filter(clip=clip).exists()
    assert phone_video.exists()  # The test's own file, not the original, is untouched.
    with pytest.raises(FileNotFoundError):
        open(original_path, 'rb')


def test_the_evidence_copy_in_the_vault_matches_its_hash_record(phone_video, tmp_path, group_key_pair):
    clip = confirmed_clip(phone_video)
    ingest.ingest(clip)
    record = HashRecord.objects.get(clip=clip)
    copy = tmp_path / 'downloaded.mov'
    with vault.open_file(clip, vault.EVIDENCE_COPY, open_data_key(clip, vault.EVIDENCE_COPY, group_key_pair)) as stored:
        copy.write_bytes(stored.read())
    assert hashing.file_hash(copy) == record.evidence_sha256
    assert [list(pair) for pair in hashing.stream_hashes(copy, 60)] == record.original_stream_hashes


def test_working_files_are_removed(phone_video, settings):
    clip = confirmed_clip(phone_video)
    ingest.ingest(clip)
    assert not [path for path in settings.QUARANTINE_DIR.iterdir() if path.name.startswith('ingest-')]


def test_a_portrait_video_has_its_upright_size(make_media, tmp_path):
    landscape = make_media('landscape.mp4', *H264, '-c:a', 'aac')
    portrait = tmp_path / 'portrait.mp4'
    run_ffmpeg('-display_rotation', '90', '-i', str(landscape), '-map', '0', '-c', 'copy', str(portrait))
    clip = confirmed_clip(portrait)
    ingest.ingest(clip)
    clip.refresh_from_db()
    assert (clip.width, clip.height) == (120, 160)


@pytest.mark.parametrize('content', [b'hello, not a video', b'\x00' * 4096])
def test_a_file_that_is_not_a_video_is_rejected(content, django_capture_on_commit_callbacks):
    # M2 done-when (d). Upload refuses such files already; ingest checks again.
    clip = Clip.objects.create(code_hash=uuid.uuid4().hex * 2)
    original = Original(clip=clip)
    original.file.save('original', ContentFile(content))
    with pytest.raises(ingest.Rejected):
        ingest.ingest(clip)
    with django_capture_on_commit_callbacks(execute=True):
        ingest.give_up(clip, 'rejected')
    clip.refresh_from_db()
    assert clip.status == Clip.Status.FAILED
    assert not Original.objects.filter(clip=clip).exists()
    assert not original.file.storage.exists(original.file.name)
    assert not HashRecord.objects.exists()


def test_an_original_that_no_longer_says_what_it_did_is_rejected(phone_video):
    clip = confirmed_clip(phone_video)
    Clip.objects.filter(pk=clip.pk).update(metadata_time=datetime.datetime(2020, 1, 1, tzinfo=UTC))
    clip.refresh_from_db()
    with pytest.raises(ingest.Rejected, match='metadata_time_differs'):
        ingest.ingest(clip)


def test_no_stored_location_is_finer_than_the_grid(phone_video, tmp_path, group_key_pair):
    # M2 done-when (g): the GPS position is kept only as its cell, nowhere else.
    clip = confirmed_clip(phone_video, metadata_resolution=10)
    ingest.ingest(clip)
    clip.refresh_from_db()
    assert grid.resolution(clip.metadata_cell) == 10

    with connection.cursor() as cursor:
        tables = [name for name in connection.introspection.table_names() if name.startswith('clips_')]
        rows = []
        for table in tables:
            cursor.execute(f'SELECT * FROM "{table}"')
            rows.extend(cursor.fetchall())
    dumped = json.dumps(rows, default=str)
    for fragment in ('48.8532', '2.3692', '48.85', 'FakePhoneMaker'):
        assert fragment not in dumped
    for kind in (vault.EVIDENCE_COPY, vault.PROXY):
        with vault.open_file(clip, kind, open_data_key(clip, kind, group_key_pair)) as stored:
            data = stored.read()
        assert b'48.8532' not in data and b'FakePhoneMaker' not in data


def test_a_coarser_grid_setting_is_respected(phone_video, settings):
    settings.GRID_H3_RESOLUTION = 9
    clip = confirmed_clip(phone_video)
    ingest.ingest(clip)
    clip.refresh_from_db()
    assert clip.status == Clip.Status.READY
    assert grid.resolution(clip.metadata_cell) == 9


def test_an_edit_made_during_ingest_is_kept(phone_video, monkeypatch):
    clip = confirmed_clip(phone_video)
    stated = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    real_make_proxy = proxy.make_proxy

    def edit_meanwhile(*args):
        Clip.objects.filter(pk=clip.pk).update(stated_time=stated)
        real_make_proxy(*args)

    monkeypatch.setattr(proxy, 'make_proxy', edit_meanwhile)
    ingest.ingest(clip)
    clip.refresh_from_db()
    assert (clip.status, clip.stated_time) == (Clip.Status.READY, stated)


def test_a_clip_deleted_during_ingest_leaves_nothing(phone_video, monkeypatch, settings):
    clip = confirmed_clip(phone_video)
    real_make_proxy = proxy.make_proxy

    def delete_meanwhile(*args):
        delete_clip(Clip.objects.get(pk=clip.pk))
        real_make_proxy(*args)

    monkeypatch.setattr(proxy, 'make_proxy', delete_meanwhile)
    ingest.ingest(clip)
    assert not Clip.objects.exists()
    assert not HashRecord.objects.exists()
    assert not [path for path in settings.VAULT_DIR.rglob('*') if path.is_file()]


def test_files_stored_before_a_failure_are_removed(phone_video, monkeypatch, settings):
    clip = confirmed_clip(phone_video)

    def broken_save(self, *args, **kwargs):
        raise OSError('disk full')

    monkeypatch.setattr(HashRecord, 'save', broken_save)
    with pytest.raises(OSError):
        ingest.ingest(clip)
    assert not [path for path in settings.VAULT_DIR.rglob('*') if path.is_file()]
    clip.refresh_from_db()
    assert clip.status == Clip.Status.PROCESSING
    assert Original.objects.filter(clip=clip).exists()


def test_files_are_copied_to_the_vault_outside_the_transaction(phone_video, monkeypatch):
    # Inside it, SQLite would hold up every other write while a large file is copied.
    clip = confirmed_clip(phone_video)
    depth_outside = len(connection.atomic_blocks)
    depths = []
    real_store_file = vault.store_file

    def store_file(*args):
        depths.append(len(connection.atomic_blocks))
        return real_store_file(*args)

    monkeypatch.setattr(vault, 'store_file', store_file)
    ingest.ingest(clip)
    clip.refresh_from_db()
    assert clip.status == Clip.Status.READY
    assert depths == [depth_outside, depth_outside]


def test_a_ready_clip_is_left_alone(phone_video):
    clip = confirmed_clip(phone_video)
    ingest.ingest(clip)
    clip.refresh_from_db()
    ingest.ingest(clip)
    assert HashRecord.objects.count() == 1


def test_an_attempt_that_runs_out_of_time_is_not_a_rejection(phone_video, settings, monkeypatch):
    clip = confirmed_clip(phone_video)
    monkeypatch.setattr(ingest.Deadline, 'remaining', lambda self: 0.0001)
    with pytest.raises(Exception) as raised:
        ingest.ingest(clip)
    assert not isinstance(raised.value, ingest.Rejected)
    clip.refresh_from_db()
    assert clip.status == Clip.Status.PROCESSING
