import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.db.models import ProtectedError

from clips import vault
from clips.editing import delete_clip
from clips.models import Clip, VaultFile, VaultText

pytestmark = pytest.mark.django_db

BASE_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
def clip():
    return Clip.objects.create(code_hash=uuid.uuid4().hex * 2)


@pytest.mark.parametrize('kind', [vault.CONTEXT, vault.CONTACT_DETAILS])
def test_text_goes_in_and_comes_out(clip, kind):
    vault.put_text(clip, kind, 'Filmed from the corner, facing the square.')
    assert vault.get_text(clip, kind) == 'Filmed from the corner, facing the square.'


def test_a_clip_with_nothing_in_the_vault_reads_as_empty(clip):
    assert vault.get_text(clip, vault.CONTEXT) == ''


def test_a_new_value_replaces_the_old_one(clip):
    vault.put_text(clip, vault.CONTACT_DETAILS, 'first handle')
    vault.put_text(clip, vault.CONTACT_DETAILS, 'second handle')
    assert vault.get_text(clip, vault.CONTACT_DETAILS) == 'second handle'
    assert list(VaultText.objects.values_list('content', flat=True)) == ['second handle']


def test_an_empty_value_removes_the_text(clip):
    vault.put_text(clip, vault.CONTEXT, 'some context')
    vault.put_text(clip, vault.CONTEXT, '')
    assert vault.get_text(clip, vault.CONTEXT) == ''
    assert not VaultText.objects.exists()


def test_kinds_are_kept_apart(clip):
    vault.put_text(clip, vault.CONTEXT, 'context')
    vault.put_text(clip, vault.CONTACT_DETAILS, 'contact')
    assert vault.get_text(clip, vault.CONTEXT) == 'context'
    assert vault.get_text(clip, vault.CONTACT_DETAILS) == 'contact'


def test_delete_all_and_clip_deletion_empty_the_vault(clip):
    other = Clip.objects.create(code_hash=uuid.uuid4().hex * 2)
    vault.put_text(clip, vault.CONTEXT, 'context')
    vault.put_text(other, vault.CONTEXT, 'other context')
    vault.delete_all(clip)
    assert vault.get_text(clip, vault.CONTEXT) == ''
    other.delete()
    assert not VaultText.objects.exists()


def test_the_text_form_of_a_vault_entry_hides_its_content(clip):
    vault.put_text(clip, vault.CONTEXT, 'a private detail')
    entry = VaultText.objects.get()
    assert 'a private detail' not in str(entry)
    assert 'a private detail' not in repr(entry)


def test_a_clip_has_no_shortcut_into_the_vault(clip):
    assert not any(name.startswith('vault') for name in dir(clip))


def test_only_the_vault_module_touches_vault_storage():
    allowed = {BASE_DIR / 'clips' / 'vault.py', BASE_DIR / 'clips' / 'models.py'}
    skipped = {'tests', 'migrations', 'var', 'media_test'}
    offenders = [
        path.relative_to(BASE_DIR)
        for path in BASE_DIR.rglob('*.py')
        if path not in allowed
        and not skipped & set(path.relative_to(BASE_DIR).parts)
        and ('VaultText' in path.read_text() or 'VaultFile' in path.read_text())
    ]
    assert offenders == []


def vault_files_on_disk():
    return {path for path in settings.VAULT_DIR.rglob('*') if path.is_file()}


@pytest.fixture
def local_file(tmp_path):
    path = tmp_path / 'work.mp4'
    path.write_bytes(b'not really a video, but the vault does not look')
    return path


@pytest.mark.parametrize('kind', [vault.EVIDENCE_COPY, vault.PROXY])
def test_a_file_goes_in_and_comes_out(clip, local_file, kind):
    vault.put_file(clip, kind, local_file, 'mp4')
    assert vault.has_file(clip, kind)
    with vault.open_file(clip, kind) as stored:
        assert stored.read() == local_file.read_bytes()


def test_files_are_stored_privately_under_random_names(clip, local_file):
    vault.put_file(clip, vault.EVIDENCE_COPY, local_file, 'mov')
    [path] = vault_files_on_disk()
    assert path.parent == settings.VAULT_DIR
    assert path.suffix == '.mov' and len(path.stem) == 32 and 'work' not in path.name
    assert path.stat().st_mode & 0o777 == 0o600
    assert local_file.exists()  # The caller deletes its own copy.


def test_a_new_file_replaces_the_old_one_on_disk(
    clip, local_file, tmp_path, django_capture_on_commit_callbacks,
):
    vault.put_file(clip, vault.PROXY, local_file, 'mp4')
    second = tmp_path / 'second.mp4'
    second.write_bytes(b'second attempt')
    with django_capture_on_commit_callbacks(execute=True):
        vault.put_file(clip, vault.PROXY, second, 'mp4')
    [path] = vault_files_on_disk()
    assert path.read_bytes() == b'second attempt'
    assert VaultFile.objects.count() == 1


def test_delete_all_removes_the_files_from_disk(clip, local_file, django_capture_on_commit_callbacks):
    other = Clip.objects.create(code_hash=uuid.uuid4().hex * 2)
    vault.put_file(clip, vault.EVIDENCE_COPY, local_file, 'mp4')
    vault.put_file(clip, vault.PROXY, local_file, 'mp4')
    vault.put_file(other, vault.PROXY, local_file, 'mp4')
    with django_capture_on_commit_callbacks(execute=True):
        vault.delete_all(clip)
    assert not vault.has_file(clip, vault.EVIDENCE_COPY) and not vault.has_file(clip, vault.PROXY)
    assert len(vault_files_on_disk()) == 1
    assert vault.has_file(other, vault.PROXY)


def test_a_clip_with_files_cannot_be_deleted_around_the_vault(clip, local_file):
    vault.put_file(clip, vault.EVIDENCE_COPY, local_file, 'mp4')
    with pytest.raises(ProtectedError):
        clip.delete()


def test_the_code_holder_deleting_a_clip_removes_its_files(
    clip, local_file, django_capture_on_commit_callbacks,
):
    vault.put_file(clip, vault.EVIDENCE_COPY, local_file, 'mp4')
    vault.put_file(clip, vault.PROXY, local_file, 'mp4')
    with django_capture_on_commit_callbacks(execute=True):
        delete_clip(clip)
    assert not Clip.objects.exists()
    assert vault_files_on_disk() == set()


def test_the_text_form_of_a_vault_file_hides_its_name(clip, local_file):
    vault.put_file(clip, vault.EVIDENCE_COPY, local_file, 'mp4')
    entry = VaultFile.objects.get()
    assert entry.file.name not in str(entry)
    assert entry.file.name not in repr(entry)
