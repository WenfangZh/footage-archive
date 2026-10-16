import uuid
from pathlib import Path

import pytest

from clips import vault
from clips.models import Clip, VaultText

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
        and 'VaultText' in path.read_text()
    ]
    assert offenders == []
