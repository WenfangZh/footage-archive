import datetime
import errno
import os
import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.core.files.base import File as BaseFile
from django.db.models import ProtectedError

from clips import vault
from clips.editing import delete_clip
from clips.models import Clip, SealedKey, VaultFile, VaultStorage, VaultText
from tests.conftest import put_vault_file as put_file

# Every file is sealed to the group keys in use: one exists in each test here.
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('group_key_pair')]

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
    data_key = put_file(clip, kind, local_file, 'mp4')
    assert vault.has_file(clip, kind)
    with vault.open_file(clip, kind, data_key) as stored:
        assert stored.size == len(local_file.read_bytes())
        assert stored.read() == local_file.read_bytes()


def test_files_are_stored_privately_under_random_names(clip, local_file):
    put_file(clip, vault.EVIDENCE_COPY, local_file, 'mov')
    [path] = vault_files_on_disk()
    assert path.parent == settings.VAULT_DIR
    assert path.suffix == '.mov' and len(path.stem) == 32 and 'work' not in path.name
    assert path.stat().st_mode & 0o777 == 0o600
    assert local_file.exists()  # The caller deletes its own copy.


def test_a_new_file_replaces_the_old_one_on_disk(
    clip, local_file, tmp_path, django_capture_on_commit_callbacks,
):
    put_file(clip, vault.PROXY, local_file, 'mp4')
    second = tmp_path / 'second.mp4'
    second.write_bytes(b'second attempt')
    with django_capture_on_commit_callbacks(execute=True):
        data_key = put_file(clip, vault.PROXY, second, 'mp4')
    assert len(vault_files_on_disk()) == 1
    with vault.open_file(clip, vault.PROXY, data_key) as stored:
        assert stored.read() == b'second attempt'
    assert VaultFile.objects.count() == 1


def test_a_stored_file_belongs_to_no_clip_until_it_is_recorded(clip, local_file):
    name, _ = vault.store_file(clip, vault.EVIDENCE_COPY, local_file, 'mp4')
    assert not VaultFile.objects.exists()
    vault.delete_unrecorded_files([name])
    assert not vault_files_on_disk()

    name, data_key = vault.store_file(clip, vault.EVIDENCE_COPY, local_file, 'mp4')
    vault.record_file(clip, vault.EVIDENCE_COPY, name, data_key)
    vault.delete_unrecorded_files([name])
    assert vault.has_file(clip, vault.EVIDENCE_COPY)
    assert len(vault_files_on_disk()) == 1


def test_a_failed_copy_leaves_no_file_and_keeps_the_others(clip, local_file, monkeypatch):
    put_file(clip, vault.EVIDENCE_COPY, local_file, 'mp4')
    before = {path: path.read_bytes() for path in vault_files_on_disk()}

    encrypting = vault._Encrypting.chunks

    def disk_fills_up(self, chunk_size=None):
        yield next(encrypting(self, chunk_size))
        raise OSError(errno.ENOSPC, 'No space left on device')

    monkeypatch.setattr(vault._Encrypting, 'chunks', disk_fills_up)
    with pytest.raises(OSError):
        vault.store_file(clip, vault.EVIDENCE_COPY, local_file, 'mp4')
    assert {path: path.read_bytes() for path in vault_files_on_disk()} == before


def test_a_name_clash_fails_without_touching_the_file_there(clip, local_file, monkeypatch):
    monkeypatch.setattr(vault.uuid, 'uuid4', lambda: uuid.UUID(int=1))
    existing = settings.VAULT_DIR / f'{uuid.UUID(int=1).hex}.mp4'
    existing.write_bytes(b'someone else\'s evidence copy')
    with pytest.raises(FileExistsError):
        vault.store_file(clip, vault.EVIDENCE_COPY, local_file, 'mp4')
    assert vault_files_on_disk() == {existing}
    assert existing.read_bytes() == b'someone else\'s evidence copy'


def test_a_file_appearing_at_the_name_meanwhile_is_left_alone(clip, local_file, monkeypatch):
    # It appears after the storage has checked the name and before it creates the file,
    # so Django's own save finds the clash. That relies on Django creating the file
    # before it reads the first chunk: if a new Django changed that, this test fails.
    check_name = VaultStorage.get_available_name

    def name_taken_meanwhile(self, name, max_length=None):
        name = check_name(self, name, max_length)
        (settings.VAULT_DIR / name).write_bytes(b"someone else's evidence copy")
        return name

    monkeypatch.setattr(VaultStorage, 'get_available_name', name_taken_meanwhile)
    with pytest.raises(FileExistsError):
        vault.store_file(clip, vault.EVIDENCE_COPY, local_file, 'mp4')
    [path] = vault_files_on_disk()
    assert path.read_bytes() == b"someone else's evidence copy"


def test_delete_all_removes_the_files_from_disk(clip, local_file, django_capture_on_commit_callbacks):
    other = Clip.objects.create(code_hash=uuid.uuid4().hex * 2)
    put_file(clip, vault.EVIDENCE_COPY, local_file, 'mp4')
    put_file(clip, vault.PROXY, local_file, 'mp4')
    put_file(other, vault.PROXY, local_file, 'mp4')
    with django_capture_on_commit_callbacks(execute=True):
        vault.delete_all(clip)
    assert not vault.has_file(clip, vault.EVIDENCE_COPY) and not vault.has_file(clip, vault.PROXY)
    assert len(vault_files_on_disk()) == 1
    assert vault.has_file(other, vault.PROXY)


def test_a_clip_with_files_cannot_be_deleted_around_the_vault(clip, local_file):
    put_file(clip, vault.EVIDENCE_COPY, local_file, 'mp4')
    with pytest.raises(ProtectedError):
        clip.delete()


def test_the_code_holder_deleting_a_clip_removes_its_files(
    clip, local_file, django_capture_on_commit_callbacks,
):
    put_file(clip, vault.EVIDENCE_COPY, local_file, 'mp4')
    put_file(clip, vault.PROXY, local_file, 'mp4')
    with django_capture_on_commit_callbacks(execute=True):
        delete_clip(clip)
    assert not Clip.objects.exists()
    assert vault_files_on_disk() == set()


def test_the_text_form_of_a_vault_file_hides_its_name(clip, local_file):
    put_file(clip, vault.EVIDENCE_COPY, local_file, 'mp4')
    entry = VaultFile.objects.get()
    assert entry.file.name not in str(entry)
    assert entry.file.name not in repr(entry)


def age(path, delta):
    moment = (datetime.datetime.now(datetime.UTC) - delta).timestamp()
    os.utime(path, (moment, moment))


def test_old_files_no_clip_refers_to_are_deleted(clip, local_file):
    put_file(clip, vault.EVIDENCE_COPY, local_file, 'mp4')
    [kept] = vault_files_on_disk()
    old_stray = settings.VAULT_DIR / f'{uuid.uuid4().hex}.mp4'
    recent_stray = settings.VAULT_DIR / f'{uuid.uuid4().hex}.mp4'
    not_ours = settings.VAULT_DIR / 'keys.bin'
    for path in (old_stray, recent_stray, not_ours):
        path.write_bytes(b'left over')
    for path in (kept, old_stray, not_ours):
        age(path, vault.STRAY_FILE_AGE + datetime.timedelta(minutes=1))

    assert vault.delete_stray_files() == 1
    assert not old_stray.exists()
    assert kept.exists() and vault.has_file(clip, vault.EVIDENCE_COPY)
    # It may belong to an ingest whose transaction has not committed yet.
    assert recent_stray.exists()
    # Only names the vault gives its files are ever swept.
    assert not_ours.exists()


def test_a_stored_file_counts_as_new_however_old_its_source(clip, local_file):
    # The sweep's margin relies on this: an old source must not make a fresh copy look stray.
    age(local_file, vault.STRAY_FILE_AGE * 3)
    put_file(clip, vault.EVIDENCE_COPY, local_file, 'mp4')
    VaultFile.objects.all().delete()  # As if its transaction had not committed yet.
    assert vault.delete_stray_files() == 0
    assert len(vault_files_on_disk()) == 1


def test_the_files_of_a_crash_after_delete_all_are_swept_up(clip, local_file):
    put_file(clip, vault.PROXY, local_file, 'mp4')
    # The callback that deletes the files never runs, as if the process died after the commit.
    vault.delete_all(clip)
    [left] = vault_files_on_disk()
    age(left, vault.STRAY_FILE_AGE + datetime.timedelta(minutes=1))
    assert vault.delete_stray_files() == 1
    assert vault_files_on_disk() == set()


def test_too_many_strays_at_once_are_all_kept():
    # As after restoring an old database: these may be the only copies.
    paths = [settings.VAULT_DIR / f'{uuid.uuid4().hex}.mp4' for _ in range(vault.MAX_STRAY_FILES + 1)]
    for path in paths:
        path.write_bytes(b'maybe the only copy')
        age(path, vault.STRAY_FILE_AGE * 2)
    with pytest.raises(vault.TooManyStrays):
        vault.delete_stray_files()
    assert all(path.exists() for path in paths)


# Encryption (ADR 0008, docs/m5-encryption-design.md §6).

def stored_bytes(clip, kind):
    return (settings.VAULT_DIR / VaultFile.objects.get(clip=clip, kind=kind).file.name).read_bytes()


# Several chunks, the last one short.
PLAINTEXT = b'0123456789abcdef' * (vault.CHUNK_SIZE // 8) + b'the end'


@pytest.fixture
def big_file(tmp_path):
    path = tmp_path / 'evidence.mp4'
    path.write_bytes(PLAINTEXT)
    return path


def test_the_vault_holds_no_plaintext_and_the_database_no_data_key(clip, big_file, group_key_pair):
    from nacl.public import SealedBox

    data_key = put_file(clip, vault.EVIDENCE_COPY, big_file, 'mp4')
    on_disk = stored_bytes(clip, vault.EVIDENCE_COPY)
    assert b'0123456789abcdef' not in on_disk and b'the end' not in on_disk
    vault_file = VaultFile.objects.get()
    assert data_key not in bytes(vault_file.key_check)
    # Only a group key's private half opens what the server keeps of the data key.
    sealed = SealedKey.objects.get(vault_file=vault_file)
    assert data_key not in bytes(sealed.sealed_data_key) and sealed.sealed_by is None
    assert SealedBox(group_key_pair).decrypt(bytes(sealed.sealed_data_key)) == data_key


def test_a_data_key_is_sealed_to_every_group_key_in_use(clip, local_file):
    from keys.models import GroupKey
    GroupKey.objects.update(status=GroupKey.Status.RETIRING)
    new = GroupKey.objects.create(public_key=os.urandom(32), status=GroupKey.Status.CHECKING)
    GroupKey.objects.create(public_key=os.urandom(32), status=GroupKey.Status.REJECTED)
    put_file(clip, vault.PROXY, local_file, 'mp4')
    sealed_to = set(SealedKey.objects.values_list('group_key__status', flat=True))
    assert sealed_to == {GroupKey.Status.RETIRING, GroupKey.Status.CHECKING} and new.sealed_keys.count() == 1


def test_no_file_is_recorded_without_a_group_key_to_seal_it_to(clip, local_file):
    from keys.models import GroupKey
    GroupKey.objects.update(status=GroupKey.Status.RETIRED)
    name, data_key = vault.store_file(clip, vault.PROXY, local_file, 'mp4')
    with pytest.raises(vault.NoGroupKey):
        vault.record_file(clip, vault.PROXY, name, data_key)
    assert not VaultFile.objects.exists()


def test_a_wrong_data_key_opens_nothing(clip, local_file):
    put_file(clip, vault.PROXY, local_file, 'mp4')
    with pytest.raises(vault.WrongKey):
        vault.open_file(clip, vault.PROXY, os.urandom(vault.DATA_KEY_BYTES))
    assert not vault.is_data_key(clip, vault.PROXY, os.urandom(vault.DATA_KEY_BYTES))


@pytest.mark.parametrize('position', [0, 1, vault.CHUNK_SIZE - 1, vault.CHUNK_SIZE, len(PLAINTEXT) - 3, len(PLAINTEXT)])
def test_reading_from_any_position_gives_the_right_bytes(clip, big_file, position):
    data_key = put_file(clip, vault.EVIDENCE_COPY, big_file, 'mp4')
    with vault.open_file(clip, vault.EVIDENCE_COPY, data_key) as stored:
        assert stored.size == len(PLAINTEXT)
        stored.seek(position)
        assert stored.read(100_000) == PLAINTEXT[position:position + 100_000]


def read_all(clip, kind, data_key):
    with vault.open_file(clip, kind, data_key) as stored:
        return stored.read()


@pytest.mark.parametrize('damage', ['cut at a chunk', 'cut inside a chunk', 'a byte altered', 'a byte added'])
def test_a_cut_off_or_altered_file_is_never_read_as_complete(clip, big_file, damage):
    data_key = put_file(clip, vault.EVIDENCE_COPY, big_file, 'mp4')
    path = settings.VAULT_DIR / VaultFile.objects.get().file.name
    stored = bytearray(path.read_bytes())
    if damage == 'cut at a chunk':
        stored = stored[:vault.HEADER_BYTES + 2 * (vault.CHUNK_SIZE + vault.ABYTES)]
    elif damage == 'cut inside a chunk':
        stored = stored[:-5]
    elif damage == 'a byte altered':
        stored[vault.HEADER_BYTES + vault.CHUNK_SIZE + 100] ^= 1
    else:
        stored += b'x'
    path.write_bytes(bytes(stored))
    with pytest.raises(vault.Unreadable):
        read_all(clip, vault.EVIDENCE_COPY, data_key)


def test_a_file_cut_at_a_chunk_boundary_fails_even_when_read_only_to_its_size(clip, big_file):
    # A server serving `size` bytes never asks past them (clips.streaming).
    data_key = put_file(clip, vault.EVIDENCE_COPY, big_file, 'mp4')
    path = settings.VAULT_DIR / VaultFile.objects.get().file.name
    path.write_bytes(path.read_bytes()[:vault.HEADER_BYTES + 2 * (vault.CHUNK_SIZE + vault.ABYTES)])
    with vault.open_file(clip, vault.EVIDENCE_COPY, data_key) as stored:
        with pytest.raises(vault.Unreadable):
            stored.read(stored.size)


@pytest.mark.parametrize('whole_chunks', [0, 2])
def test_a_cut_leaving_what_looks_like_an_empty_final_chunk_is_refused(clip, big_file, whole_chunks):
    # An empty final chunk ends only an empty file: after whole chunks, or alone at the start of
    # a file that held bytes, such a block is what is left of a cut, and holds no bytes to read.
    data_key = put_file(clip, vault.EVIDENCE_COPY, big_file, 'mp4')
    path = settings.VAULT_DIR / VaultFile.objects.get().file.name
    path.write_bytes(path.read_bytes()[:vault.HEADER_BYTES + whole_chunks * (vault.CHUNK_SIZE + vault.ABYTES) + vault.ABYTES])
    with pytest.raises(vault.Unreadable):
        with vault.open_file(clip, vault.EVIDENCE_COPY, data_key) as stored:
            stored.read(stored.size)


def test_an_empty_file_goes_in_and_comes_out(clip, tmp_path):
    empty = tmp_path / 'empty.mp4'
    empty.write_bytes(b'')
    data_key = put_file(clip, vault.PROXY, empty, 'mp4')
    with vault.open_file(clip, vault.PROXY, data_key) as stored:
        assert stored.size == 0 and stored.read() == b''


def test_a_file_moved_to_another_clip_or_kind_does_not_decrypt(clip, local_file):
    other = Clip.objects.create(code_hash=uuid.uuid4().hex * 2)
    data_key = put_file(clip, vault.EVIDENCE_COPY, local_file, 'mp4')
    other_key = put_file(other, vault.PROXY, local_file, 'mp4')
    moved = VaultFile.objects.get(clip=clip).file.name
    # The file and its key check, as a database writer could swap them.
    VaultFile.objects.filter(clip=other).update(file=moved, key_check=VaultFile.objects.get(clip=clip).key_check)
    with pytest.raises(vault.Unreadable):
        read_all(other, vault.PROXY, data_key)
    VaultFile.objects.filter(clip=clip).update(kind=vault.PROXY)
    VaultFile.objects.filter(clip=other).delete()
    with pytest.raises(vault.Unreadable):
        read_all(clip, vault.PROXY, data_key)
    assert other_key != data_key


def test_no_key_opens_a_file_stored_before_encryption(clip, local_file):
    put_file(clip, vault.PROXY, local_file, 'mp4')
    VaultFile.objects.update(key_check=None)
    with pytest.raises(vault.WrongKey):
        vault.open_file(clip, vault.PROXY, os.urandom(vault.DATA_KEY_BYTES))


def test_encrypt_vault_encrypts_files_stored_before_encryption_once(clip, local_file, group_key_pair, django_capture_on_commit_callbacks):
    from django.core.management import call_command

    from tests.conftest import open_data_key
    name = vault.vault_storage().save(f'{uuid.uuid4().hex}.mp4', BaseFile(local_file.open('rb')))
    VaultFile.objects.create(clip=clip, kind=vault.PROXY, file=name)
    with django_capture_on_commit_callbacks(execute=True):
        call_command('encrypt_vault', stdout=open(os.devnull, 'w'))
    [path] = vault_files_on_disk()
    assert path.name != name and local_file.read_bytes() not in path.read_bytes()
    with vault.open_file(clip, vault.PROXY, open_data_key(clip, vault.PROXY, group_key_pair)) as stored:
        assert stored.read() == local_file.read_bytes()
    assert vault.encrypt_plaintext_files() == 0
    assert vault_files_on_disk() == {path}


def test_encrypt_vault_needs_a_group_key(clip, local_file):
    from django.core.management import CommandError, call_command

    from keys.models import GroupKey
    GroupKey.objects.all().delete()
    with pytest.raises(CommandError):
        call_command('encrypt_vault')
