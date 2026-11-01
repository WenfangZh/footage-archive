"""A clip's page and preview, for a lawyer whose case found it (SPEC.md §7)."""

import datetime
from unittest import mock

import h3.api.basic_int as h3
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import LawyerProfile
from audit.models import Action, AuditEntry
from cases.models import Case
from clips import grid, vault
from clips.models import Clip, ClipTag, Edit
from tests.conftest import (
    BASTILLE, log_in_with_second_factor, make_case, open_page_text, put_vault_file, with_code_holder_key, with_data_key,
)

pytestmark = pytest.mark.django_db

User = get_user_model()
CENTRE = grid.cell_for_point(*BASTILLE, 11)
START = datetime.datetime(2026, 6, 15, 16, tzinfo=datetime.UTC)
PROXY_BYTES = bytes(range(256)) * 40


def make_lawyer(email):
    profile = LawyerProfile.objects.create(user=User.objects.create_user(email), bar='Paris')
    profile.verified_via, profile.vouched_by = 'The bar directory', 'A defence organisation'
    profile.save()
    profile.change_status(LawyerProfile.Status.LAWYER)
    return profile


@pytest.fixture
def lawyer():
    return make_lawyer('lawyer@example.org')


@pytest.fixture
def lawyer_client(lawyer):
    return log_in_with_second_factor(Client(), lawyer.user)


@pytest.fixture
def case(lawyer):
    return make_case(lawyer, rings=2, starts_at=START, ends_at=START + datetime.timedelta(hours=1))


@pytest.fixture
def clip(tmp_path, group_key_pair, lawyer_client):
    """A ready clip with a proxy, whose data key the lawyer's browser has handed over."""
    clip = Clip.objects.create(
        code_hash='1' * 64, status=Clip.Status.READY, stated_time=START + datetime.timedelta(minutes=10),
        metadata_time=START + datetime.timedelta(minutes=10, seconds=30), duration=datetime.timedelta(seconds=42),
        stated_cells=[CENTRE], metadata_cell=grid.coarsen(CENTRE, 10),
        as_uploaded_stated_time=START, as_uploaded_stated_cells=[CENTRE],
        as_uploaded_tags={'default': ['arrest'], 'custom': ['Rue de la Roquette']},
    )
    with_code_holder_key(clip)
    source = tmp_path / 'proxy.mp4'
    source.write_bytes(PROXY_BYTES)
    with_data_key(lawyer_client, vault.PROXY, put_vault_file(clip, vault.PROXY, source, 'mp4'))
    return clip


def test_a_lawyer_sees_a_painted_place_as_cells_a_size_and_a_real_cells_centre(case, clip, lawyer_client, settings):
    settings.LANGUAGE_CODE = 'en'
    # A stretch of street: three cells in a row, the middle one in the middle.
    row = [h3.grid_ring(CENTRE, 1)[0], CENTRE]
    row.append(next(cell for cell in h3.grid_ring(CENTRE, 1) if h3.grid_distance(cell, row[0]) == 2))
    Clip.objects.filter(pk=clip.pk).update(stated_cells=sorted(row))

    page = lawyer_client.get(url('clip', case, clip)).content.decode()

    latitude, longitude = grid.centre(CENTRE)
    width = round(grid.span_metres(row), -1)
    assert f'3 cells around {latitude:.5f}, {longitude:.5f}, about {width:.0f} m across' in page
    # As uploaded, still the one cell.
    assert 'the cell around' in page


def access_entries():
    return AuditEntry.objects.filter(action__in=[Action.PREVIEW, Action.CLIP_VIEW])


def url(name, case, clip):
    return reverse(f'lawyer:{name}', kwargs={'case_id': case.pk, 'clip_id': clip.pk})


def test_a_lawyer_sees_a_matching_clip_with_its_values(case, clip, lawyer_client, settings, group_key_pair):
    settings.LANGUAGE_CODE = 'en'
    ClipTag.objects.create(clip=clip, key='use_of_force', source=ClipTag.Source.CODE_HOLDER)
    Edit.objects.create(clip=clip, field=Edit.EditableField.STATED_TIME)
    vault.put_text(clip, vault.CONTEXT, 'Filmed from the corner of the square.')
    response = lawyer_client.get(url('clip', case, clip))
    assert response.status_code == 200 and response.context['match'] == 'core'
    page = response.content.decode()
    for shown in (
        'Use of force <small>(code holder)</small>', 'Tags as uploaded: Arrest, Rue de la Roquette',
        'Stated time: ', 'in the four hours from', '42 s', 'the cell around',
        'about 60 m across',
        # Stated, metadata and as-uploaded times, in Paris time.
        '15 June 2026 18:10:00', '15 June 2026 18:10:30', '15 June 2026 18:00:00',
    ):
        assert shown in page, shown
    assert url('preview', case, clip) in page
    # The context comes encrypted, for the page's script to open with the group key.
    assert 'Filmed from the corner' not in page
    assert open_page_text(response.context['context'], group_key_pair) == 'Filmed from the corner of the square.'


def test_uploader_text_is_escaped(case, clip, lawyer_client):
    # Texts are never in the page as text: the script adds them as text nodes, never markup.
    ClipTag.objects.create(clip=clip, custom_name='<script>x</script>', source=ClipTag.Source.CODE_HOLDER)
    vault.put_text(clip, vault.CONTEXT, 'Seen from "there" & <b>here</b>')
    page = lawyer_client.get(url('clip', case, clip)).content.decode()
    assert '<script>x' not in page and '&lt;script&gt;x&lt;/script&gt;' in page
    assert 'here' not in page


def test_viewing_the_page_is_logged_once_in_two_hours_and_is_not_a_preview(case, clip, lawyer_client):
    for _ in range(2):
        lawyer_client.get(url('clip', case, clip))
    entry = AuditEntry.objects.get()
    assert (entry.action, entry.case, entry.clip_id) == (Action.CLIP_VIEW, case, clip.pk)
    later = timezone.now() + datetime.timedelta(hours=2, minutes=1)
    with mock.patch('django.utils.timezone.now', return_value=later):
        lawyer_client.get(url('clip', case, clip))
    assert AuditEntry.objects.filter(action=Action.CLIP_VIEW).count() == 2
    assert not AuditEntry.objects.filter(action=Action.PREVIEW).exists()


@pytest.mark.parametrize('method', ['head', 'post'])
def test_the_page_is_only_a_get(case, clip, lawyer_client, method):
    assert getattr(lawyer_client, method)(url('clip', case, clip)).status_code == 405
    assert not AuditEntry.objects.exists()


def test_a_visit_logs_both_the_view_and_the_preview(case, clip, lawyer_client):
    lawyer_client.get(url('clip', case, clip))
    lawyer_client.get(url('preview', case, clip), HTTP_RANGE='bytes=0-10')
    assert sorted(AuditEntry.objects.values_list('action', flat=True)) == sorted([Action.CLIP_VIEW, Action.PREVIEW])


def test_a_view_in_another_case_is_logged_for_that_case(lawyer, case, clip, lawyer_client):
    other = make_case(lawyer, 'Other', rings=1, starts_at=START, ends_at=START + datetime.timedelta(hours=1))
    lawyer_client.get(url('clip', case, clip))
    lawyer_client.get(url('clip', other, clip))
    assert set(AuditEntry.objects.filter(action=Action.CLIP_VIEW).values_list('case', flat=True)) == {case.pk, other.pk}


def test_a_time_mismatch_is_highlighted(case, clip, lawyer_client, settings):
    settings.LANGUAGE_CODE = 'en'
    assert lawyer_client.get(url('clip', case, clip)).context['mismatch'] is None
    Clip.objects.filter(pk=clip.pk).update(metadata_time=clip.stated_time + datetime.timedelta(minutes=20))
    page = lawyer_client.get(url('clip', case, clip)).content.decode()
    assert 'The stated time is 20 minutes away from the metadata time.' in page
    # Highlighted only past the threshold, not at it.
    settings.TIME_MISMATCH_MINUTES = 20
    assert lawyer_client.get(url('clip', case, clip)).context['mismatch'] is None


def test_a_margin_match_says_so(lawyer, clip, lawyer_client):
    case = make_case(lawyer, cells={h3.grid_ring(CENTRE, 1)[0]}, margin_rings=1,
                     starts_at=START, ends_at=START + datetime.timedelta(hours=1))
    assert lawyer_client.get(url('clip', case, clip)).context['match'] == 'margin'


def test_the_preview_plays_in_byte_ranges_and_is_logged(case, clip, lawyer_client):
    response = lawyer_client.get(url('preview', case, clip), HTTP_RANGE='bytes=100-199')
    assert response.status_code == 206 and b''.join(response.streaming_content) == PROXY_BYTES[100:200]
    assert response['Content-Type'] == 'video/mp4' and 'no-store' in response['Cache-Control']
    assert response['X-Content-Type-Options'] == 'nosniff' and response['Accept-Ranges'] == 'bytes'
    assert response['Content-Range'] == f'bytes 100-199/{len(PROXY_BYTES)}'
    entry = AuditEntry.objects.get(action=Action.PREVIEW)
    assert (entry.case, entry.clip_id, entry.lawyer_profile) == (case, clip.pk, case.lawyer_profile)


def test_a_preview_again_within_two_hours_is_not_logged_again(case, clip, lawyer_client):
    for _ in range(3):
        lawyer_client.get(url('preview', case, clip), HTTP_RANGE='bytes=0-10')
    assert AuditEntry.objects.filter(action=Action.PREVIEW).count() == 1
    later = timezone.now() + datetime.timedelta(hours=2, minutes=1)
    with mock.patch('django.utils.timezone.now', return_value=later):
        lawyer_client.get(url('preview', case, clip), HTTP_RANGE='bytes=0-10')
    assert AuditEntry.objects.filter(action=Action.PREVIEW).count() == 2


def test_the_same_clip_in_another_case_is_logged_for_that_case(lawyer, case, clip, lawyer_client):
    other = make_case(lawyer, 'Other', rings=1, starts_at=START, ends_at=START + datetime.timedelta(hours=1))
    lawyer_client.get(url('preview', case, clip))
    lawyer_client.get(url('preview', other, clip))
    assert set(AuditEntry.objects.filter(action=Action.PREVIEW).values_list('case', flat=True)) == {case.pk, other.pk}


@pytest.mark.parametrize('name', ['clip', 'preview'])
def test_a_clip_that_does_not_match_the_case_is_not_found(lawyer, clip, lawyer_client, name):
    elsewhere = make_case(lawyer, cells={grid.cell_for_point(BASTILLE[0] + 0.1, BASTILLE[1], 11)},
                          starts_at=START, ends_at=START + datetime.timedelta(hours=1))
    assert lawyer_client.get(url(name, elsewhere, clip)).status_code == 404
    assert not AuditEntry.objects.exists()


@pytest.mark.parametrize('name', ['clip', 'preview'])
def test_another_lawyer_s_case_is_not_found(case, clip, name):
    other = log_in_with_second_factor(Client(), make_lawyer('other@example.org').user)
    assert other.get(url(name, case, clip)).status_code == 404
    assert not access_entries().exists()


@pytest.mark.parametrize('name', ['clip', 'preview'])
def test_a_closed_case_allows_no_preview(case, clip, lawyer_client, name):
    Case.objects.filter(pk=case.pk).close(Case.ClosedBecause.LAWYER)
    assert lawyer_client.get(url(name, case, clip)).status_code == 404
    assert not access_entries().exists()


@pytest.mark.parametrize('method', ['head', 'post'])
def test_a_preview_is_only_a_get(case, clip, lawyer_client, method):
    assert getattr(lawyer_client, method)(url('preview', case, clip)).status_code == 405
    assert not AuditEntry.objects.exists()


def test_a_proxy_missing_from_disk_is_not_found_and_not_logged(case, clip, lawyer_client):
    vault.vault_storage().delete(vault.VaultFile.objects.get(clip=clip, kind=vault.PROXY).file.name)
    assert lawyer_client.get(url('preview', case, clip)).status_code == 404
    assert not AuditEntry.objects.exists()


@pytest.mark.parametrize('name', ['clip', 'preview'])
def test_a_case_waiting_for_approval_or_refused_allows_no_preview(lawyer, clip, lawyer_client, name):
    admin = User.objects.create_superuser('admin@example.org')
    window = {'starts_at': START, 'ends_at': START + datetime.timedelta(hours=3)}
    waiting = make_case(lawyer, 'Waiting', approval_reason='Two charges.', **window)
    refused = make_case(lawyer, 'Refused', approval_reason='Two charges.', **window)
    refused.approval.decide(False, admin)
    for case in (waiting, refused):
        assert lawyer_client.get(url(name, case, clip)).status_code == 404
    assert not access_entries().exists()


@pytest.mark.parametrize('name', ['clip', 'preview'])
def test_a_clip_not_ready_is_not_found(case, clip, lawyer_client, name):
    Clip.objects.filter(pk=clip.pk).update(status=Clip.Status.PROCESSING)
    assert lawyer_client.get(url(name, case, clip)).status_code == 404


def test_search_results_link_to_the_clip_page(case, clip, lawyer_client):
    page = lawyer_client.post(reverse('lawyer:search', kwargs={'case_id': case.pk})).content.decode()
    assert url('clip', case, clip) in page


def make_clip_for(lawyer, tmp_path):
    """A case for `lawyer` and a ready clip it matches, with a proxy and an evidence copy, and
    their data keys. A group key must be in use."""
    case = make_case(lawyer, rings=2, starts_at=START, ends_at=START + datetime.timedelta(hours=1))
    clip = Clip.objects.create(
        code_hash='2' * 64, status=Clip.Status.READY, stated_time=START + datetime.timedelta(minutes=10),
        stated_cells=[CENTRE],
    )
    data_keys = {}
    for kind, name in ((vault.PROXY, 'proxy.mp4'), (vault.EVIDENCE_COPY, 'evidence.mp4')):
        source = tmp_path / name
        source.write_bytes(PROXY_BYTES)
        data_keys[kind] = put_vault_file(clip, kind, source, 'mp4')
    return case, clip, data_keys


def test_contact_details_left_by_the_code_holder_come_with_a_reminder(case, clip, lawyer_client, settings, group_key_pair):
    settings.LANGUAGE_CODE = 'en'
    assert 'Contact details' not in lawyer_client.get(url('clip', case, clip)).content.decode()
    vault.put_text(clip, vault.CONTACT_DETAILS, '@filmer_example <b>')
    response = lawyer_client.get(url('clip', case, clip))
    page = response.content.decode()
    assert 'Contact details' in page and 'filmer_example' not in page
    assert open_page_text(response.context['contact_details'], group_key_pair) == '@filmer_example <b>'
    assert 'Use them only for this case' in page
