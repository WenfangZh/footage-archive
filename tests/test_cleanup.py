import datetime
import os
import uuid
from pathlib import Path

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.utils import timezone

from clips.cleanup import delete_unconfirmed_originals
from clips.models import Clip, Original

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def empty_quarantine(settings):
    """Start from an empty quarantine: files other tests left there would be counted.

    It is the test run's temporary folder, never the developer's var/.
    """
    assert not settings.DATA_DIR.is_relative_to(Path(__file__).resolve().parent.parent)
    for path in settings.QUARANTINE_DIR.rglob('*'):
        if path.is_file():
            path.unlink()


@pytest.fixture
def minutes(settings):
    return settings.UNCONFIRMED_ORIGINAL_MINUTES


def make_original(received_minutes_ago, confirmed=False):
    original = Original(received_at=timezone.now() - datetime.timedelta(minutes=received_minutes_ago))
    if confirmed:
        original.clip = Clip.objects.create(code_hash=uuid.uuid4().hex * 2)
    original.file.save('original', ContentFile(b'synthetic bytes'))
    return original


def path_of(original, settings):
    return settings.QUARANTINE_DIR / original.file.name


def age(path, minutes_ago):
    moment = (timezone.now() - datetime.timedelta(minutes=minutes_ago)).timestamp()
    os.utime(path, (moment, moment))


def test_an_unconfirmed_original_is_deleted_after_the_deadline(settings, minutes):
    old, recent = make_original(minutes + 1), make_original(minutes - 1)
    assert delete_unconfirmed_originals() == (1, 0)
    assert not Original.objects.filter(pk=old.pk).exists()
    assert not path_of(old, settings).exists()
    assert Original.objects.filter(pk=recent.pk).exists()
    assert path_of(recent, settings).exists()


def test_a_confirmed_original_is_never_deleted_for_age(settings, minutes):
    confirmed = make_original(minutes * 1000, confirmed=True)
    age(path_of(confirmed, settings), minutes * 1000)
    assert delete_unconfirmed_originals() == (0, 0)
    assert path_of(confirmed, settings).exists()
    assert Original.objects.filter(pk=confirmed.pk).exists()


def test_old_stray_files_go_and_recent_ones_stay(settings, minutes):
    old_stray = settings.QUARANTINE_DIR / uuid.uuid4().hex
    recent_stray = settings.QUARANTINE_DIR / uuid.uuid4().hex
    old_incoming = settings.FILE_UPLOAD_TEMP_DIR / 'tmpold.upload.mp4'
    arriving = settings.FILE_UPLOAD_TEMP_DIR / 'tmpnow.upload.mp4'
    for path in (old_stray, recent_stray, old_incoming, arriving):
        path.write_bytes(b'synthetic bytes')
    age(old_stray, minutes + 1)
    age(old_incoming, minutes + 1)

    assert delete_unconfirmed_originals() == (0, 2)
    assert not old_stray.exists() and not old_incoming.exists()
    assert recent_stray.exists() and arriving.exists()
    assert settings.FILE_UPLOAD_TEMP_DIR.is_dir()


def test_an_old_file_of_a_waiting_original_is_kept(settings, minutes):
    # A recently received original may have an old modification time, copied from the phone.
    original = make_original(minutes - 1)
    age(path_of(original, settings), minutes * 10)
    assert delete_unconfirmed_originals() == (0, 0)
    assert path_of(original, settings).exists()


def test_the_command_reports_counts_only(capsys, minutes):
    make_original(minutes + 1)
    call_command('delete_unconfirmed_originals')
    assert capsys.readouterr().out.strip() == 'Deleted 1 unconfirmed originals, 0 partial uploads and 0 stray files.'
