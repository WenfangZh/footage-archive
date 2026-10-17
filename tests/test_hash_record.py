import datetime
import uuid

import pytest

from clips.editing import delete_clip
from clips.models import Clip, HashRecord

pytestmark = pytest.mark.django_db

UTC = datetime.UTC


@pytest.fixture
def clip():
    return Clip.objects.create(code_hash=uuid.uuid4().hex * 2)


def make_record(clip, hashed_at=datetime.datetime(2026, 5, 1, 13, 47, 5, tzinfo=UTC)):
    return HashRecord.objects.create(
        clip=clip,
        original_sha256='a' * 64,
        original_stream_hashes=[['v', 'b' * 64], ['a', 'c' * 64]],
        original_hashed_at=hashed_at,
        evidence_sha256='d' * 64,
        evidence_stream_hashes=[['v', 'b' * 64], ['a', 'c' * 64]],
        evidence_hashed_at=hashed_at,
    )


def test_times_are_kept_only_to_their_block(clip):
    # 13:47 UTC is 15:47 in Paris (summer time), in the 12h-16h block.
    record = make_record(clip)
    record.refresh_from_db()
    block = datetime.datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
    assert record.original_hashed_at == block
    assert record.evidence_hashed_at == block


def test_the_record_outlives_its_clip(clip):
    record = make_record(clip)
    delete_clip(clip)
    record.refresh_from_db()
    assert record.clip is None
    assert record.original_sha256 == 'a' * 64


def test_a_record_cannot_be_changed(clip):
    record = make_record(clip)
    record.original_sha256 = 'e' * 64
    with pytest.raises(ValueError):
        record.save()
    record.refresh_from_db()
    assert record.original_sha256 == 'a' * 64


def test_a_clip_starts_as_processing(clip):
    assert clip.status == Clip.Status.PROCESSING
