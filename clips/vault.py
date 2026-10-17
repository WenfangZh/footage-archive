"""The vault: the only way in or out of what the archive holds for lawyers only (SPEC.md §6).

It holds a clip's context and contact details, and its evidence copy and proxy, as
plaintext for now. Encryption comes in M5 behind these same functions, so no caller
has to change. M6 adds messages.

Nothing here logs content. A new value replaces the old one in place, and an empty
value removes it: old values of context and contact details are never kept (SPEC.md §5).

Files are stored under random names. A file is removed from disk only once the
database has let go of it, so a failure never leaves a row pointing at nothing; at
worst it leaves a file no row points to.
"""

import uuid

from django.core.files import File
from django.db import transaction

from .models import VaultFile, VaultText, vault_storage

CONTEXT = VaultText.Kind.CONTEXT
CONTACT_DETAILS = VaultText.Kind.CONTACT_DETAILS
EVIDENCE_COPY = VaultFile.Kind.EVIDENCE_COPY
PROXY = VaultFile.Kind.PROXY


def put_text(clip, kind, text):
    """Store `text` as the clip's context or contact details, replacing any old value."""
    if text:
        VaultText.objects.update_or_create(clip=clip, kind=kind, defaults={'content': text})
    else:
        VaultText.objects.filter(clip=clip, kind=kind).delete()


def get_text(clip, kind):
    """The clip's context or contact details, or '' if there are none."""
    entry = VaultText.objects.filter(clip=clip, kind=kind).first()
    return entry.content if entry else ''


def put_file(clip, kind, source_path, extension):
    """Store a copy of the local file at `source_path` as the clip's evidence copy or proxy.

    It replaces any file of that kind the clip already had. The caller deletes
    `source_path`. Returns the stored file's name, for delete_unrecorded_files.
    """
    storage = vault_storage()
    with open(source_path, 'rb') as source:
        name = storage.save(f'{uuid.uuid4().hex}.{extension}', File(source))
    try:
        with transaction.atomic():
            old = VaultFile.objects.select_for_update().filter(clip=clip, kind=kind).first()
            VaultFile.objects.update_or_create(clip=clip, kind=kind, defaults={'file': name})
    except BaseException:
        storage.delete(name)
        raise
    if old is not None:
        transaction.on_commit(lambda: storage.delete(old.file.name))
    return name


def delete_unrecorded_files(names):
    """Delete the named stored files that no clip refers to, such as after a rollback."""
    storage = vault_storage()
    recorded = set(VaultFile.objects.filter(file__in=names).values_list('file', flat=True))
    for name in names:
        if name not in recorded:
            storage.delete(name)


def has_file(clip, kind):
    return VaultFile.objects.filter(clip=clip, kind=kind).exists()


def open_file(clip, kind):
    """The clip's evidence copy or proxy, opened for reading in binary; the caller closes it."""
    return VaultFile.objects.get(clip=clip, kind=kind).file.open('rb')


def delete_all(clip):
    """Remove everything the vault holds for a clip."""
    VaultText.objects.filter(clip=clip).delete()
    names = list(VaultFile.objects.filter(clip=clip).values_list('file', flat=True))
    VaultFile.objects.filter(clip=clip).delete()
    storage = vault_storage()

    def delete_files():
        for name in names:
            storage.delete(name)

    transaction.on_commit(delete_files)
