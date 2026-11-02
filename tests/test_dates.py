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
from tests.conftest import log_in_with_second_factor, make_lawyer, typed_time
from tests.test_case_pages import FORM as CASE_FORM
from tests.test_code_holder import EDIT_URL, form_values, open_clip, uploaded  # noqa: F401 (a fixture)
from tests.test_confirm import full_form, start_upload, video  # noqa: F401 (a fixture)

pytestmark = pytest.mark.django_db

ARCHIVE_FORMATS = {'DATE_FORMAT', 'DATETIME_FORMAT', 'TIME_FORMAT', 'Y-m-d H:i:s'}


def test_templates_use_only_the_archive_date_formats():
    templates = [path for path in Path(settings.BASE_DIR).glob('*/templates/**/*.html')]
    templates += list(Path(settings.BASE_DIR, 'templates').glob('**/*.html'))
    used = {fmt for path in templates for fmt in re.findall(r'\|date:"([^"]*)"', path.read_text())}
    assert used and used <= ARCHIVE_FORMATS, used - ARCHIVE_FORMATS


@pytest.fixture
def clip_waiting(settings):
    from clips.models import Clip
    settings.QUEUE_ALERT_CLIPS = 0
    return Clip.objects.create(status=Clip.Status.PROCESSING)


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


# Typed wrongly in the year, month, day and time boxes (SPEC.md §12 M8.5), with what the form says.
WRONG_TIMES = {
    'impossible': (lambda name: typed_time(name, '2026-02-31T10:00'), 'Enter a date that exists.'),
    'partly empty': (lambda name: {**typed_time(name, '2026-06-15T10:00'), f'{name}_day': ''}, 'Enter the year, month, day and time.'),
    'too early': (lambda name: typed_time(name, '1999-12-31T10:00'), 'Enter a date between 2000 and next year.'),
}


@pytest.mark.parametrize('wrong', WRONG_TIMES)
@pytest.mark.parametrize('form', ['upload', 'code holder', 'new case'])
def test_every_typed_time_refuses_a_wrong_one_and_says_why(client, request, form, wrong):
    times, message = WRONG_TIMES[wrong]
    if form == 'upload':
        url = start_upload(client, request.getfixturevalue('video'))
        response = client.post(url, {**full_form(), **times('stated_time')}, headers={'accept-language': 'en'})
    elif form == 'code holder':
        # Confirming an upload seals its texts to the group key.
        request.getfixturevalue('group_key_pair')
        _, code = request.getfixturevalue('uploaded')
        values = form_values(open_clip(client, code).content.decode())
        response = client.post(EDIT_URL, {**values, **times('stated_time')}, headers={'accept-language': 'en'})
    else:
        client = log_in_with_second_factor(client, make_lawyer().user)
        response = client.post(reverse('lawyer:new_case'), {**CASE_FORM, **times('starts_at')}, headers={'accept-language': 'en'})
    assert response.status_code == 400
    assert message in response.content.decode()
