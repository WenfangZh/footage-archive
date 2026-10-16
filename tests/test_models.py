import datetime
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction

from clips import grid
from clips.models import Clip, ClipTag, Edit, Original, Precision

pytestmark = pytest.mark.django_db

# A public place (Place de la Bastille, Paris), not anyone's position.
LATITUDE, LONGITUDE = 48.8532, 2.3692
PRECISE_TIME = datetime.datetime(2026, 6, 15, 21, 59, 59, tzinfo=datetime.UTC)
ITS_BLOCK = datetime.datetime(2026, 6, 15, 18, 0, tzinfo=datetime.UTC)


def make_clip(**fields):
    fields.setdefault('code_hash', uuid.uuid4().hex * 2)
    return Clip.objects.create(**fields)


def test_clip_ids_are_random_uuids():
    first, second = make_clip(), make_clip()
    assert first.id.version == 4
    assert first.id != second.id


def test_upload_time_is_kept_only_to_its_block():
    clip = make_clip(upload_time=PRECISE_TIME)
    assert clip.upload_time == ITS_BLOCK
    clip.refresh_from_db()
    assert clip.upload_time == ITS_BLOCK


def test_edit_time_is_kept_only_to_its_block():
    edit = Edit.objects.create(clip=make_clip(), field=Edit.EditableField.CONTEXT, edited_at=PRECISE_TIME)
    edit.refresh_from_db()
    assert edit.edited_at == ITS_BLOCK


@pytest.mark.parametrize('precision, resolution', [
    (Precision.EXACT_SPOT, 11), (Precision.STREET, 10), (Precision.NEIGHBOURHOOD, 9),
])
def test_stated_location_keeps_only_the_cell_for_its_precision(precision, resolution):
    clip = Clip(code_hash='0' * 64)
    clip.set_stated_location(LATITUDE, LONGITUDE, precision)
    clip.save()
    clip.refresh_from_db()
    assert clip.stated_precision == precision
    assert grid.resolution(clip.stated_cell) == resolution
    assert clip.stated_cell == grid.cell_for_point(LATITUDE, LONGITUDE, resolution)


@pytest.mark.parametrize('fields', [
    {'stated_cell': grid.cell_for_point(LATITUDE, LONGITUDE, 11)},
    {'stated_precision': Precision.STREET},
    {'as_uploaded_stated_cell': grid.cell_for_point(LATITUDE, LONGITUDE, 11)},
    {'as_uploaded_stated_precision': Precision.STREET},
])
def test_a_location_needs_both_its_cell_and_its_precision(fields):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_clip(**fields)


def test_metadata_location_resolutions_are_11_to_9():
    clip = Clip(code_hash='0' * 64)
    for resolution in (11, 10, 9):
        clip.set_metadata_location(LATITUDE, LONGITUDE, resolution)
        assert grid.resolution(clip.metadata_cell) == resolution
    for resolution in (12, 8):
        with pytest.raises(ValueError):
            clip.set_metadata_location(LATITUDE, LONGITUDE, resolution)


@pytest.mark.parametrize('field, value', [
    ('metadata_time', PRECISE_TIME + datetime.timedelta(minutes=1)),
    ('metadata_time', None),
    ('upload_time', ITS_BLOCK + datetime.timedelta(hours=4)),
    ('code_hash', 'f' * 64),
    ('as_uploaded_stated_time', None),
    ('as_uploaded_stated_precision', Precision.NEIGHBOURHOOD),
    ('as_uploaded_tags', {'default': ['arrest', 'tear_gas'], 'custom': []}),
])
def test_metadata_time_and_as_uploaded_values_cannot_change(field, value):
    clip = Clip(
        code_hash='0' * 64, metadata_time=PRECISE_TIME, as_uploaded_stated_time=PRECISE_TIME,
        as_uploaded_tags={'default': ['arrest'], 'custom': []},
    )
    clip.as_uploaded_stated_cell = grid.cell_for_point(LATITUDE, LONGITUDE, 10)
    clip.as_uploaded_stated_precision = Precision.STREET
    clip.save()
    setattr(clip, field, value)
    with pytest.raises(ValueError, match=f'{field} cannot be changed'):
        clip.save()


def test_stated_values_can_change():
    clip = make_clip(stated_time=PRECISE_TIME)
    clip.stated_time = PRECISE_TIME + datetime.timedelta(hours=1)
    clip.set_stated_location(LATITUDE, LONGITUDE, Precision.NEIGHBOURHOOD)
    clip.save()


def test_metadata_location_can_be_made_coarser():
    clip = Clip(code_hash='0' * 64)
    clip.set_metadata_location(LATITUDE, LONGITUDE, 11)
    clip.save()
    clip.coarsen_metadata_location(9)
    clip.save()
    clip.refresh_from_db()
    assert clip.metadata_cell == grid.cell_for_point(LATITUDE, LONGITUDE, 9)


def test_metadata_location_cannot_be_made_finer_moved_or_removed():
    clip = Clip(code_hash='0' * 64)
    clip.set_metadata_location(LATITUDE, LONGITUDE, 10)
    clip.save()
    elsewhere = grid.cell_for_point(43.2965, 5.3698, 10)  # Marseille, Vieux-Port
    for cell in (grid.cell_for_point(LATITUDE, LONGITUDE, 11), elsewhere, None):
        clip.metadata_cell = cell
        with pytest.raises(ValueError, match='only be made coarser'):
            clip.save()


def test_metadata_location_cannot_be_added_after_upload():
    clip = make_clip()
    clip.set_metadata_location(LATITUDE, LONGITUDE, 9)
    with pytest.raises(ValueError, match='only be made coarser'):
        clip.save()


def test_a_tag_is_a_default_tag_or_a_custom_tag():
    clip = make_clip()
    ClipTag.objects.create(clip=clip, key='arrest', source=ClipTag.Source.CODE_HOLDER)
    ClipTag.objects.create(clip=clip, custom_name='Pont Neuf', source=ClipTag.Source.CODE_HOLDER)
    for fields in ({}, {'key': 'tear_gas', 'custom_name': 'gaz'}):
        with pytest.raises(IntegrityError), transaction.atomic():
            ClipTag.objects.create(clip=clip, source=ClipTag.Source.CODE_HOLDER, **fields)


def test_unknown_default_tag_keys_are_invalid():
    tag = ClipTag(clip=make_clip(), key='suspect', source=ClipTag.Source.CODE_HOLDER)
    with pytest.raises(ValidationError):
        tag.full_clean()


def test_a_tag_appears_once_per_source():
    clip = make_clip()
    ClipTag.objects.create(clip=clip, key='arrest', source=ClipTag.Source.CODE_HOLDER)
    ClipTag.objects.create(clip=clip, key='arrest', source=ClipTag.Source.LAWYER)
    with pytest.raises(IntegrityError), transaction.atomic():
        ClipTag.objects.create(clip=clip, key='arrest', source=ClipTag.Source.CODE_HOLDER)


def test_original_is_stored_in_quarantine_under_a_random_name(settings):
    original = Original()
    original.file.save('IMG_1234 Rue de Rivoli.MOV', ContentFile(b'not really a video'))
    path = settings.QUARANTINE_DIR / original.file.name
    assert path.is_file()
    assert path.stat().st_mode & 0o777 == 0o600
    assert 'IMG' not in original.file.name and 'Rivoli' not in original.file.name
    assert len(original.file.name) == 32


def test_received_time_is_kept_only_to_its_block_once_confirmed():
    original = Original.objects.create(file='x', received_at=PRECISE_TIME)
    assert original.received_at == PRECISE_TIME
    original.clip = make_clip()
    original.save()
    original.refresh_from_db()
    assert original.received_at == ITS_BLOCK


def test_deleting_a_clip_deletes_its_tags_edits_and_original_record():
    clip = make_clip()
    ClipTag.objects.create(clip=clip, key='arrest', source=ClipTag.Source.CODE_HOLDER)
    Edit.objects.create(clip=clip, field=Edit.EditableField.TAGS)
    Original.objects.create(file='x', clip=clip)
    clip.delete()
    assert not ClipTag.objects.exists() and not Edit.objects.exists() and not Original.objects.exists()
