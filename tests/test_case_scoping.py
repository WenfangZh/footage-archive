"""A lawyer reaches only their own cases (SPEC.md §5, §7): every page whose address names a
case is not found for another lawyer, and changes nothing.

The pages are found by walking the URL configuration, as in tests/test_roles.py, so every
page added later with a case in its address is checked here too.
"""

import pytest
from django.db import transaction
from django.test import Client
from django.urls import URLResolver, get_resolver, reverse

from clips import vault
from clips.models import ClipTag, HashRecord
from tests.conftest import database_dump, log_in_with_second_factor, put_vault_file, with_data_key
from tests.test_clip_pages import START, make_lawyer
from tests.test_clip_pages import case, clip, lawyer, lawyer_client  # noqa: F401
from tests.test_roles import LAWYER_AREA

pytestmark = pytest.mark.django_db


def case_pages():
    """The name and the address parts of every lawyer page whose address names a case."""
    found = []

    def walk(patterns, route, namespace):
        for pattern in patterns:
            full_route = route + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, full_route, ':'.join(filter(None, [namespace, pattern.namespace])))
            elif full_route.startswith(LAWYER_AREA) and 'case_id' in pattern.pattern.converters:
                found.append((':'.join(filter(None, [namespace, pattern.name])), list(pattern.pattern.converters)))

    walk(get_resolver().url_patterns, '', '')
    return found


class Undo(Exception):
    pass


def undone(request):
    """The response to a request whose changes to the database are then undone."""
    try:
        with transaction.atomic():
            response = request()
            raise Undo
    except Undo:
        return response


@pytest.fixture
def address_parts(case, clip, tmp_path, lawyer_client):
    """A value for each part of a case page's address, which the case's own lawyer reaches:
    a ready clip the case matches, with an evidence copy, a hash record and a tag."""
    source = tmp_path / 'evidence.mov'
    source.write_bytes(b'evidence')
    with_data_key(lawyer_client, vault.EVIDENCE_COPY, put_vault_file(clip, vault.EVIDENCE_COPY, source, 'mov'))
    HashRecord.objects.create(
        clip=clip, original_sha256='a' * 64, original_stream_hashes=[], original_hashed_at=START,
        evidence_sha256='b' * 64, evidence_stream_hashes=[], evidence_hashed_at=START,
    )
    tag = ClipTag.objects.create(clip=clip, key='arrest', source=ClipTag.Source.CODE_HOLDER)
    return {'case_id': case.pk, 'clip_id': clip.pk, 'tag_id': tag.pk, 'file': 'proxy', 'kind': 'doubt'}


def test_the_case_pages_are_found():
    names = [name for name, _ in case_pages()]
    assert 'lawyer:case' in names and 'lawyer:download' in names


def test_no_lawyer_reaches_another_lawyer_s_case_or_changes_anything_through_it(address_parts, lawyer_client):
    other = log_in_with_second_factor(Client(), make_lawyer('other@example.org').user)
    before = database_dump()
    for name, parts in case_pages():
        url = reverse(name, kwargs={part: address_parts[part] for part in parts})
        methods = []
        for method in ['get', 'post']:
            own = undone(lambda: getattr(lawyer_client, method)(url))
            if own.status_code == 405:
                continue
            methods.append(method)
            # The case's own lawyer reaches the page, so the other lawyer's 404 comes from the case.
            assert own.status_code != 404, (url, method)
            assert getattr(other, method)(url).status_code == 404, (url, method)
        assert methods, url
    assert database_dump() == before
