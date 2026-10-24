"""Downloading a matching clip's evidence copy and its hash record (SPEC.md §7)."""

import datetime
import json
from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from audit.models import Action, AuditEntry
from cases.models import Case
from clips import vault
from clips.models import HashRecord
from tests.conftest import log_in_with_second_factor, make_case, put_vault_file, with_data_key
from tests.test_clip_pages import START, make_lawyer  # noqa: F401 - fixtures below
from tests.test_clip_pages import case, clip, lawyer, lawyer_client  # noqa: F401

pytestmark = pytest.mark.django_db

EVIDENCE_BYTES = b'evidence ' * 500


@pytest.fixture
def evidence(clip, tmp_path, lawyer_client):
    """The clip with an evidence copy, whose data key the lawyer's browser has handed over."""
    source = tmp_path / 'evidence.mov'
    source.write_bytes(EVIDENCE_BYTES)
    with_data_key(lawyer_client, vault.EVIDENCE_COPY, put_vault_file(clip, vault.EVIDENCE_COPY, source, 'mov'))
    return clip


def url(name, case, clip):
    return reverse(f'lawyer:{name}', kwargs={'case_id': case.pk, 'clip_id': clip.pk})


def downloads():
    return AuditEntry.objects.filter(action=Action.DOWNLOAD)


def test_a_lawyer_downloads_the_evidence_copy_as_it_is(case, evidence, lawyer_client):
    response = lawyer_client.get(url('download', case, evidence))
    assert response.status_code == 200 and b''.join(response.streaming_content) == EVIDENCE_BYTES
    assert response['Content-Type'] == 'video/quicktime'
    assert response['Content-Disposition'] == f'attachment; filename="clip-{evidence.pk}.mov"'
    assert 'no-store' in response['Cache-Control'] and response['X-Content-Type-Options'] == 'nosniff'
    entry = downloads().get()
    assert (entry.case, entry.clip_id, entry.lawyer_profile) == (case, evidence.pk, case.lawyer_profile)


def test_every_download_is_logged(case, evidence, lawyer_client):
    lawyer_client.get(url('download', case, evidence))
    lawyer_client.get(url('download', case, evidence))
    assert downloads().count() == 2


@pytest.mark.parametrize('asked', ['bytes=0-99', 'bytes=1-', f'bytes=-{len(EVIDENCE_BYTES)}', 'bytes=-10'])
def test_any_first_request_that_serves_bytes_is_logged(case, evidence, lawyer_client, asked):
    lawyer_client.get(url('download', case, evidence), HTTP_RANGE=asked)
    assert downloads().count() == 1


def test_a_download_resumed_soon_after_is_the_same_download(case, evidence, lawyer_client):
    lawyer_client.get(url('download', case, evidence), HTTP_RANGE='bytes=0-99')
    lawyer_client.get(url('download', case, evidence), HTTP_RANGE='bytes=100-')
    assert downloads().count() == 1


def test_a_request_that_serves_nothing_is_not_logged(case, evidence, lawyer_client):
    assert lawyer_client.get(url('download', case, evidence), HTTP_RANGE='bytes=99999-').status_code == 416
    assert not downloads().exists()


@pytest.mark.parametrize('method', ['head', 'post'])
def test_a_download_is_only_a_get(case, evidence, lawyer_client, method):
    assert getattr(lawyer_client, method)(url('download', case, evidence)).status_code == 405
    assert not downloads().exists()


def test_the_file_is_closed_if_logging_fails(case, evidence, lawyer_client, monkeypatch):
    opened = []
    real_open = vault.open_file
    monkeypatch.setattr(vault, 'open_file', lambda *args: opened.append(real_open(*args)) or opened[-1])
    monkeypatch.setattr('cases.clip_pages.record', mock.Mock(side_effect=RuntimeError))
    with pytest.raises(RuntimeError):
        lawyer_client.get(url('download', case, evidence))
    assert opened and opened[0].closed


def test_the_hash_record_comes_as_json(case, evidence, lawyer_client):
    block = datetime.datetime(2026, 6, 15, 14, tzinfo=datetime.UTC)
    stored = HashRecord.objects.create(
        clip=evidence, original_sha256='a' * 64, original_stream_hashes=[['v', 'b' * 64], ['a', 'c' * 64]],
        original_hashed_at=block, evidence_sha256='d' * 64, evidence_stream_hashes=[['v', 'b' * 64]],
        evidence_hashed_at=block,
    )
    response = lawyer_client.get(url('hash_record', case, evidence))
    assert response.status_code == 200 and response['Content-Type'] == 'application/json'
    assert response['Content-Disposition'] == f'attachment; filename="clip-{evidence.pk}-hash-record.json"'
    data = json.loads(response.content)
    assert set(data) == {'hash_record', 'clip', 'original', 'evidence_copy'}
    assert set(data['original']) == {'sha256', 'streams', 'hashed_in_4_hour_block_from'}
    assert data['hash_record'] == str(stored.pk) and data['clip'] == str(evidence.pk)
    assert data['original']['sha256'] == 'a' * 64
    assert data['original']['streams'] == [{'kind': 'video', 'sha256': 'b' * 64}, {'kind': 'audio', 'sha256': 'c' * 64}]
    assert data['evidence_copy']['sha256'] == 'd' * 64
    assert data['evidence_copy']['hashed_in_4_hour_block_from'].startswith('2026-06-15T')


@pytest.mark.parametrize('name', ['download', 'hash_record'])
def test_a_closed_case_allows_no_download(case, evidence, lawyer_client, name):
    Case.objects.filter(pk=case.pk).close(Case.ClosedBecause.LAWYER)
    assert lawyer_client.get(url(name, case, evidence)).status_code == 404
    assert not downloads().exists()


@pytest.mark.parametrize('name', ['download', 'hash_record'])
def test_another_lawyer_cannot_download(case, evidence, name):
    other = log_in_with_second_factor(Client(), make_lawyer('other@example.org').user)
    assert other.get(url(name, case, evidence)).status_code == 404
    assert not downloads().exists()


def test_a_clip_without_an_evidence_copy_or_hash_record_is_not_found(case, clip, lawyer_client):
    assert lawyer_client.get(url('download', case, clip)).status_code == 404
    assert lawyer_client.get(url('hash_record', case, clip)).status_code == 404


def test_an_old_download_does_not_cover_a_later_partial_request(case, evidence, lawyer_client):
    earlier = timezone.now() - datetime.timedelta(hours=2, minutes=1)
    with mock.patch('django.utils.timezone.now', return_value=earlier):
        lawyer_client.get(url('download', case, evidence))
    lawyer_client.get(url('download', case, evidence), HTTP_RANGE='bytes=100-')
    assert downloads().count() == 2


def test_a_download_in_another_case_does_not_cover_this_one(lawyer, case, evidence, lawyer_client):
    other = make_case(lawyer, 'Other', rings=1, starts_at=START, ends_at=START + datetime.timedelta(hours=1))
    lawyer_client.get(url('download', other, evidence))
    lawyer_client.get(url('download', case, evidence), HTTP_RANGE='bytes=100-')
    assert set(downloads().values_list('case', flat=True)) == {other.pk, case.pk}


@pytest.mark.parametrize('asked', ['bytes=5-2', 'bytes=0-1,5-9', 'bytes=-', 'items=0-9'])
def test_a_range_that_cannot_be_read_serves_the_whole_file_and_is_logged(case, evidence, lawyer_client, asked):
    response = lawyer_client.get(url('download', case, evidence), HTTP_RANGE=asked)
    assert response.status_code == 200 and b''.join(response.streaming_content) == EVIDENCE_BYTES
    assert downloads().count() == 1


@pytest.mark.parametrize('method', ['head', 'post'])
def test_the_hash_record_is_only_a_get_and_never_cached(case, evidence, lawyer_client, method):
    assert getattr(lawyer_client, method)(url('hash_record', case, evidence)).status_code == 405
