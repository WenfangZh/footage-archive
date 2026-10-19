"""Downloading a matching clip's evidence copy and its hash record (SPEC.md §7)."""

import datetime
import json

import pytest
from django.test import Client
from django.urls import reverse

from audit.models import Action, AuditEntry
from cases.models import Case
from clips import vault
from clips.models import HashRecord
from tests.conftest import log_in_with_second_factor
from tests.test_clip_pages import START, make_lawyer  # noqa: F401 - fixtures below
from tests.test_clip_pages import case, clip, lawyer, lawyer_client  # noqa: F401

pytestmark = pytest.mark.django_db

EVIDENCE_BYTES = b'evidence ' * 500


@pytest.fixture
def evidence(clip, tmp_path):
    source = tmp_path / 'evidence.mov'
    source.write_bytes(EVIDENCE_BYTES)
    vault.record_file(clip, vault.EVIDENCE_COPY, vault.store_file(source, 'mov'))
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


@pytest.mark.parametrize('asked, logged', [
    ('bytes=0-99', True),
    ('bytes=100-', False),          # a download resumed: the same download
    (f'bytes=-{len(EVIDENCE_BYTES)}', True),  # the whole file, from its end
    ('bytes=-10', False),
    ('bytes=99999-', False),        # nothing to serve
])
def test_a_download_is_logged_when_it_asks_for_the_first_byte(case, evidence, lawyer_client, asked, logged):
    lawyer_client.get(url('download', case, evidence), HTTP_RANGE=asked)
    assert downloads().exists() is logged


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
