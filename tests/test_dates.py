"""Dates read 2026-11-01 and times 2026-11-01 18:42 in both languages, seconds stay where a
page shows them, and a time kept only to its four-hour block says so (SPEC.md §12 M8)."""

import datetime
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.urls import reverse

from alerts.checks import check_ingest_queue
from alerts.models import Alert

pytestmark = pytest.mark.django_db

ARCHIVE_FORMATS = {'DATE_FORMAT', 'DATETIME_FORMAT', 'TIME_FORMAT', 'Y-m-d H:i:s'}


def test_templates_use_only_the_archive_date_formats():
    templates = [path for path in Path(settings.BASE_DIR).glob('*/templates/**/*.html')]
    templates += list(Path(settings.BASE_DIR, 'templates').glob('**/*.html'))
    used = {fmt for path in templates for fmt in re.findall(r'\|date:"([^"]*)"', path.read_text())}
    assert used and used <= ARCHIVE_FORMATS, used - ARCHIVE_FORMATS


@pytest.mark.parametrize('language', ['fr', 'en'])
def test_the_admin_reads_alert_times_as_the_archive_writes_them(admin_client, clip_waiting, language):
    paris = ZoneInfo('Europe/Paris')
    # A backed-up queue at 18:42 is kept as its block, from 16:00; a failed login exactly.
    check_ingest_queue(now=datetime.datetime(2026, 11, 1, 18, 42, tzinfo=paris))
    Alert.objects.create(kind=Alert.Kind.FAILED_LOGINS, raised_at=datetime.datetime(2026, 11, 1, 18, 42, tzinfo=paris))
    admin_client.cookies['django_language'] = language
    page = admin_client.get(reverse('manage:home')).content.decode()
    block = {'fr': '2026-11-01, dans les quatre heures à partir de 16:00', 'en': '2026-11-01, in the four hours from 16:00'}
    assert block[language] in page
    assert '2026-11-01 18:42' in page
    assert '2026-11-01 16:00' not in page


@pytest.fixture
def clip_waiting(settings):
    from clips.models import Clip
    settings.QUEUE_ALERT_CLIPS = 0
    return Clip.objects.create(status=Clip.Status.PROCESSING)
