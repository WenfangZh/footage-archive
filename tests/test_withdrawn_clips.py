"""The admin's withdrawn clips (SPEC.md §7, M6.2): listed by identifier only, and deleted by the
admin, keeping their hash record and audit entries, with an entry that names no lawyer; no lawyer
sees the list, and no clip that is not withdrawn can be deleted from it."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from audit.models import Action, AuditEntry
from clips.models import Clip, ClipTag, HashRecord, Report
from tests.conftest import log_in_with_second_factor
from tests.test_reports import Lawyer, clip  # noqa: F401 (the clip fixture)

pytestmark = pytest.mark.django_db

ENGLISH = {'accept-language': 'en'}
LIST_URL = reverse('manage:withdrawn_clips')


@pytest.fixture
def admin():
    return get_user_model().objects.create_superuser('admin@example.org')


@pytest.fixture
def admin_client(admin):
    return log_in_with_second_factor(Client(), admin)


@pytest.fixture
def withdrawn(clip):  # noqa: F811
    now = timezone.now()
    HashRecord.objects.create(
        clip=clip, original_sha256='a' * 64, original_stream_hashes=[], original_hashed_at=now,
        evidence_sha256='b' * 64, evidence_stream_hashes=[], evidence_hashed_at=now,
    )
    first, second = Lawyer('first@example.org'), Lawyer('second@example.org')
    first.report(clip, Report.Kind.INAPPROPRIATE)
    second.report(clip, Report.Kind.INAPPROPRIATE, confirm_withdrawal='yes')
    assert Clip.objects.get(pk=clip.pk).status == Clip.Status.WITHDRAWN
    return clip


def delete(client, clip_id, **fields):
    return client.post(reverse('manage:delete_withdrawn_clip', kwargs={'clip_id': clip_id}), fields, headers=ENGLISH)


def test_the_admin_sees_withdrawn_clips_by_identifier_only(admin_client, withdrawn):
    ready = Clip.objects.create(code_hash='c' * 64, status=Clip.Status.READY)

    content = admin_client.get(LIST_URL, headers=ENGLISH).content.decode()

    assert str(withdrawn.pk) in content and str(ready.pk) not in content
    assert 'arrest' not in content.lower() and 'example.org' not in content


def test_no_lawyer_sees_the_list_or_deletes(withdrawn):
    lawyer = Lawyer('third@example.org')

    assert lawyer.client.get(LIST_URL).status_code == 403
    assert delete(lawyer.client, withdrawn.pk, confirm_delete='yes').status_code == 403
    assert Clip.objects.filter(pk=withdrawn.pk).exists()


def test_the_admin_deletes_a_withdrawn_clip_keeping_its_hash_record_and_audit_entries(admin, admin_client, withdrawn):
    logged = list(AuditEntry.objects.filter(clip_id=withdrawn.pk).values_list('pk', flat=True))

    response = delete(admin_client, withdrawn.pk, confirm_delete='yes')

    assert response.status_code == 302
    assert not Clip.objects.filter(pk=withdrawn.pk).exists()
    assert not Report.objects.exists() and not ClipTag.objects.exists()
    record = HashRecord.objects.get()
    assert record.clip is None and record.evidence_sha256 == 'b' * 64
    assert set(logged) < set(AuditEntry.objects.filter(clip_id=withdrawn.pk).values_list('pk', flat=True))
    entry = AuditEntry.objects.get(action=Action.WITHDRAWN_CLIP_DELETION)
    assert (entry.admin, entry.lawyer_profile, entry.case, entry.clip_id) == (admin, None, None, withdrawn.pk)


def test_deleting_needs_the_box_ticked(admin_client, withdrawn):
    delete(admin_client, withdrawn.pk)

    assert Clip.objects.filter(pk=withdrawn.pk).exists()
    assert not AuditEntry.objects.filter(action=Action.WITHDRAWN_CLIP_DELETION).exists()


def test_a_clip_that_is_not_withdrawn_cannot_be_deleted_from_here(admin_client, clip):  # noqa: F811
    delete(admin_client, clip.pk, confirm_delete='yes')

    assert Clip.objects.filter(pk=clip.pk).exists()
    assert not AuditEntry.objects.filter(action=Action.WITHDRAWN_CLIP_DELETION).exists()
