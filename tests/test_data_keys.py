"""Serving encrypted vault files with the data key a lawyer's browser hands over
(docs/m5-encryption-design.md §6.2, §6.3; ADR 0008).

The clip page's script opens a file's data key with the group key and posts it; the server
checks it and returns it in a cookie that only that clip's preview and download in that case
receive. The preview and download decrypt with it, byte ranges included, and refuse without it.
"""

import base64
import datetime
import json
import logging

import pytest
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.urls import reverse
from nacl.public import SealedBox

from accounts.models import LawyerProfile
from audit.models import Action, AuditEntry
from cases.models import Case
from clips import grid, vault
from clips.models import Clip, Precision, VaultFile
from tests.conftest import (
    BASTILLE, hand_over, log_in_with_second_factor, make_case, put_vault_file, with_data_key,
)

pytestmark = pytest.mark.django_db

START = datetime.datetime(2026, 6, 15, 16, tzinfo=datetime.UTC)
# Three chunks, the last one short, so that ranges near the end cross into the final chunk.
PLAINTEXT = bytes(range(256)) * (2 * vault.CHUNK_SIZE // 256) + b'the last bytes of the file'


def make_lawyer(email):
    from django.contrib.auth import get_user_model
    profile = LawyerProfile.objects.create(user=get_user_model().objects.create_user(email), bar='Paris')
    profile.verified_via, profile.vouched_by = 'The bar directory', 'A defence organisation'
    profile.save()
    profile.change_status(LawyerProfile.Status.LAWYER)
    return profile


@pytest.fixture
def lawyer():
    return make_lawyer('lawyer@example.org')


@pytest.fixture
def client_(lawyer):
    return log_in_with_second_factor(Client(), lawyer.user)


@pytest.fixture
def case(lawyer):
    return make_case(lawyer, starts_at=START, ends_at=START + datetime.timedelta(hours=1))


@pytest.fixture
def clip(tmp_path, group_key_pair):
    """A ready clip with both files, with their data keys as `data_keys`."""
    clip = Clip.objects.create(
        code_hash='1' * 64, status=Clip.Status.READY, stated_time=START + datetime.timedelta(minutes=10),
        stated_cell=grid.cell_for_point(*BASTILLE, 11), stated_precision=Precision.EXACT_SPOT,
    )
    source = tmp_path / 'video.mp4'
    source.write_bytes(PLAINTEXT)
    clip.data_keys = {kind: put_vault_file(clip, kind, source, 'mp4') for kind in (vault.PROXY, vault.EVIDENCE_COPY)}
    return clip


def url(name, case, clip):
    return reverse(f'lawyer:{name}', kwargs={'case_id': case.pk, 'clip_id': clip.pk})


def served(response):
    return b''.join(response.streaming_content)


# Handing the data key over.

def test_the_page_carries_each_files_data_key_sealed_to_the_group_key(case, clip, client_, group_key_pair):
    page = client_.get(url('clip', case, clip))
    for name, kind in (('proxy_keys', vault.PROXY), ('evidence_keys', vault.EVIDENCE_COPY)):
        [sealed] = json.loads(page.context[name])
        assert sealed['group_public_key'] == base64.b64encode(bytes(group_key_pair.public_key)).decode()
        opened = SealedBox(group_key_pair).decrypt(base64.b64decode(sealed['sealed_data_key']))
        assert opened == clip.data_keys[kind]


@pytest.mark.parametrize('kind', [vault.PROXY, vault.EVIDENCE_COPY])
def test_a_right_data_key_comes_back_in_a_cookie_for_this_clip_in_this_case_only(case, clip, client_, kind):
    response = hand_over(client_, case, clip, kind, clip.data_keys[kind])
    assert response.status_code == 204
    [cookie] = response.cookies.values()
    assert cookie['path'] == url('clip', case, clip)
    assert cookie['httponly'] and cookie['secure'] and cookie['samesite'] == 'Strict'
    assert cookie['max-age'] == 2 * 60 * 60


def test_a_wrong_data_key_is_refused_and_sets_no_cookie(case, clip, client_):
    response = hand_over(client_, case, clip, vault.PROXY, clip.data_keys[vault.EVIDENCE_COPY])
    assert response.status_code == 403 and not response.cookies


@pytest.mark.parametrize('value', ['', 'not base64!', base64.b64encode(b'short').decode()])
def test_a_data_key_that_cannot_be_one_is_refused(case, clip, client_, value):
    response = client_.post(reverse('lawyer:data_key', kwargs={'case_id': case.pk, 'clip_id': clip.pk, 'file': 'proxy'}), {'data_key': value})
    assert response.status_code == 400 and not response.cookies


def test_a_data_key_is_taken_only_in_a_case_that_matches_the_clip(lawyer, case, clip):
    other_lawyer = make_lawyer('other@example.org')
    other_client = log_in_with_second_factor(Client(), other_lawyer.user)
    assert hand_over(other_client, case, clip, vault.PROXY, clip.data_keys[vault.PROXY]).status_code == 404
    elsewhere = make_case(other_lawyer, starts_at=START + datetime.timedelta(days=3), ends_at=START + datetime.timedelta(days=3, hours=1))
    assert hand_over(other_client, elsewhere, clip, vault.PROXY, clip.data_keys[vault.PROXY]).status_code == 404
    Case.objects.filter(pk=case.pk).close(Case.ClosedBecause.LAWYER)
    own_client = log_in_with_second_factor(Client(), lawyer.user)
    assert hand_over(own_client, case, clip, vault.PROXY, clip.data_keys[vault.PROXY]).status_code == 404


def test_handing_over_is_only_a_post_for_a_known_file(case, clip, client_):
    path = reverse('lawyer:data_key', kwargs={'case_id': case.pk, 'clip_id': clip.pk, 'file': 'proxy'})
    assert client_.get(path).status_code == 405
    other = reverse('lawyer:data_key', kwargs={'case_id': case.pk, 'clip_id': clip.pk, 'file': 'original'})
    assert client_.post(other, {'data_key': base64.b64encode(clip.data_keys[vault.PROXY]).decode()}).status_code == 404


# Serving with it (design §6.2).

@pytest.mark.parametrize('name, kind', [('preview', vault.PROXY), ('download', vault.EVIDENCE_COPY)])
def test_with_the_right_data_key_the_whole_file_is_served(case, clip, client_, name, kind):
    hand_over(client_, case, clip, kind, clip.data_keys[kind])
    response = client_.get(url(name, case, clip))
    assert response.status_code == 200 and int(response['Content-Length']) == len(PLAINTEXT)
    assert served(response) == PLAINTEXT


@pytest.mark.parametrize('name, kind', [('preview', vault.PROXY), ('download', vault.EVIDENCE_COPY)])
@pytest.mark.parametrize('asked, start, end', [
    ('bytes=0-99', 0, 99),
    (f'bytes={vault.CHUNK_SIZE - 10}-{vault.CHUNK_SIZE + 9}', vault.CHUNK_SIZE - 10, vault.CHUNK_SIZE + 9),
    (f'bytes={len(PLAINTEXT) - 30}-', len(PLAINTEXT) - 30, len(PLAINTEXT) - 1),
    ('bytes=-26', len(PLAINTEXT) - 26, len(PLAINTEXT) - 1),
])
def test_byte_ranges_serve_the_right_bytes(case, clip, client_, name, kind, asked, start, end):
    hand_over(client_, case, clip, kind, clip.data_keys[kind])
    response = client_.get(url(name, case, clip), HTTP_RANGE=asked)
    assert response.status_code == 206
    assert response['Content-Range'] == f'bytes {start}-{end}/{len(PLAINTEXT)}'
    assert served(response) == PLAINTEXT[start:end + 1]


def test_an_unsatisfiable_range_serves_nothing(case, clip, client_):
    hand_over(client_, case, clip, vault.PROXY, clip.data_keys[vault.PROXY])
    response = client_.get(url('preview', case, clip), HTTP_RANGE=f'bytes={len(PLAINTEXT)}-')
    assert response.status_code == 416 and response['Content-Range'] == f'bytes */{len(PLAINTEXT)}'


@pytest.mark.parametrize('name, kind', [('preview', vault.PROXY), ('download', vault.EVIDENCE_COPY)])
@pytest.mark.parametrize('cookie', ['none', 'wrong key', 'the other file', 'not a key'])
def test_without_the_right_data_key_nothing_is_served_or_logged(case, clip, client_, name, kind, cookie):
    other = vault.PROXY if kind == vault.EVIDENCE_COPY else vault.EVIDENCE_COPY
    if cookie == 'wrong key':
        with_data_key(client_, kind, bytes(32))
    elif cookie == 'the other file':
        with_data_key(client_, kind, clip.data_keys[other])
    elif cookie == 'not a key':
        from cases.clip_pages import COOKIES
        client_.cookies[COOKIES[kind]] = 'garbage'
    response = client_.get(url(name, case, clip))
    assert response.status_code == 403
    assert not AuditEntry.objects.filter(action__in=[Action.PREVIEW, Action.DOWNLOAD]).exists()


def test_a_damaged_file_is_never_served_as_complete(case, clip, client_):
    hand_over(client_, case, clip, vault.EVIDENCE_COPY, clip.data_keys[vault.EVIDENCE_COPY])
    path = settings.VAULT_DIR / VaultFile.objects.get(clip=clip, kind=vault.EVIDENCE_COPY).file.name
    stored = bytearray(path.read_bytes())
    stored[-10] ^= 1  # In the final chunk.
    path.write_bytes(bytes(stored))
    response = client_.get(url('download', case, clip))
    with pytest.raises(vault.Unreadable):
        served(response)


def test_a_file_cut_short_of_any_chunk_is_not_found(case, clip, client_):
    hand_over(client_, case, clip, vault.PROXY, clip.data_keys[vault.PROXY])
    path = settings.VAULT_DIR / VaultFile.objects.get(clip=clip, kind=vault.PROXY).file.name
    path.write_bytes(path.read_bytes()[:vault.HEADER_BYTES + 5])
    assert client_.get(url('preview', case, clip)).status_code == 404


# No data key is kept (§12): not in the database, the cache or a log.

def test_no_data_key_is_stored_or_logged(case, clip, client_, caplog):
    caplog.set_level(logging.DEBUG)
    for kind, name in ((vault.PROXY, 'preview'), (vault.EVIDENCE_COPY, 'download')):
        hand_over(client_, case, clip, kind, clip.data_keys[kind])
        served(client_.get(url(name, case, clip), HTTP_RANGE='bytes=0-9'))
        hand_over(client_, case, clip, kind, bytes(32))  # A wrong one, refused.
    forms = []
    for data_key in clip.data_keys.values():
        forms += [data_key, data_key.hex().encode(), base64.b64encode(data_key), base64.urlsafe_b64encode(data_key).rstrip(b'=')]
    with connection.cursor() as cursor:
        tables = connection.introspection.table_names(cursor)
        rows = []
        for table in tables:
            cursor.execute(f'SELECT * FROM "{table}"')
            rows.extend(cursor.fetchall())
    stored = b''.join(
        bytes(value) if isinstance(value, (bytes, memoryview)) else str(value).encode() for row in rows for value in row
    )
    cached = b''.join(repr(value).encode() for value in cache._cache.values()) if hasattr(cache, '_cache') else b''
    logged = '\n'.join(record.getMessage() for record in caplog.records).encode()
    for form in forms:
        assert form not in stored and form not in cached and form not in logged
