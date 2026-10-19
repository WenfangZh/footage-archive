"""The vault: the only way in or out of what the archive holds for lawyers only (SPEC.md §6).

It holds a clip's context and contact details, and its evidence copy and proxy, as
plaintext for now. Encryption comes in M5 behind these same functions, so no caller
has to change. M6 adds messages.

Nothing here logs content. A new value replaces the old one in place, and an empty
value removes it: old values of context and contact details are never kept (SPEC.md §5).

Files are stored under random names. A file is removed from disk only once the
database has let go of it, so a failure never leaves a row pointing at nothing; at
worst, if the process dies in between, it leaves a file no row points to, which
delete_stray_files removes once it is old enough.
"""

import datetime
import logging
import re
import uuid
from pathlib import PurePath

from django.core.files import File
from django.db import transaction
from django.utils import timezone

from .models import VaultFile, VaultText, vault_storage

logger = logging.getLogger(__name__)

CONTEXT = VaultText.Kind.CONTEXT
CONTACT_DETAILS = VaultText.Kind.CONTACT_DETAILS
EVIDENCE_COPY = VaultFile.Kind.EVIDENCE_COPY
PROXY = VaultFile.Kind.PROXY

# A file no row points to is left over from a crash once it is this old. Ingest stores
# its files and then records them in one short transaction, which together never last
# this long; deleting a file not yet recorded would lose an evidence copy. This relies
# on store_file writing each file anew, so that its modification time is when it was stored.
STRAY_FILE_AGE = datetime.timedelta(days=1)
# The names store_file gives files (before VaultStorage refused clashes, Django could add
# '_' and 7 characters to avoid one).
# Nothing else in the vault folder is ever swept.
# More strays than this at once means something else went wrong, such as a database
# restored from an old backup: then nothing is deleted, since these may be the only copies.
MAX_STRAY_FILES = 10
VAULT_FILE_NAME = re.compile(r'[0-9a-f]{32}(_[A-Za-z0-9]{7})?\.[A-Za-z0-9]+')


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


def store_file(source_path, extension):
    """Store a copy of the local file at `source_path`, belonging to no clip yet; returns its name.

    It touches no database row, so a caller can copy large files before opening its
    transaction, then record them with record_file inside it. Until it does, the file
    is unrecorded: delete_unrecorded_files removes it, and so would delete_stray_files
    once it is old enough.

    If the copy fails partway (a full disk, say), the partial file is removed again,
    so retries do not pile up copies of up to 2 GB.
    """
    storage = vault_storage()
    name = f'{uuid.uuid4().hex}.{extension}'
    with open(source_path, 'rb') as source:
        content = _Copy(source)
        try:
            return storage.save(name, content)
        except BaseException:
            # Django has created the file by the time it first asks for a chunk, and the
            # storage refuses a name that is taken, so then the file at `name` is ours.
            # Before that, nothing was written and a file at `name` is not ours to touch.
            if content.started:
                try:
                    storage.delete(name)
                except OSError:
                    # delete_stray_files gets it later. The name is random: safe to log.
                    logger.warning('Could not remove the partial vault file %s', name)
            raise


class _Copy(File):
    """A file being copied into the vault, which notes whether the copying has begun."""

    started = False

    def chunks(self, chunk_size=None):
        self.started = True
        yield from super().chunks(chunk_size)


def record_file(clip, kind, name):
    """Make the stored file `name` the clip's evidence copy or proxy, replacing any old one."""
    with transaction.atomic():
        old = VaultFile.objects.select_for_update().filter(clip=clip, kind=kind).first()
        VaultFile.objects.update_or_create(clip=clip, kind=kind, defaults={'file': name})
    if old is not None:
        storage = vault_storage()
        transaction.on_commit(lambda: storage.delete(old.file.name))


def delete_unrecorded_files(names):
    """Delete the named stored files that no clip refers to, such as after a rollback."""
    storage = vault_storage()
    recorded = set(VaultFile.objects.filter(file__in=names).values_list('file', flat=True))
    for name in names:
        if name not in recorded:
            storage.delete(name)


def has_file(clip, kind):
    return VaultFile.objects.filter(clip=clip, kind=kind).exists()


def extension(clip, kind):
    """The file extension of the clip's evidence copy or proxy, such as 'mp4'."""
    return PurePath(VaultFile.objects.get(clip=clip, kind=kind).file.name).suffix.lstrip('.').lower()


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


def delete_stray_files(now=None):
    """Delete files that no clip refers to and that are older than STRAY_FILE_AGE; returns how many.

    Over MAX_STRAY_FILES, it deletes none and raises TooManyStrays instead.
    """
    now = now or timezone.now()
    storage = vault_storage()
    known = set(VaultFile.objects.values_list('file', flat=True))
    strays = []
    for name in storage.listdir('')[1]:
        if not VAULT_FILE_NAME.fullmatch(name) or name in known:
            continue
        try:
            if now - storage.get_modified_time(name) >= STRAY_FILE_AGE:
                strays.append(name)
        except FileNotFoundError:  # Deleted meanwhile.
            continue
    if len(strays) > MAX_STRAY_FILES:
        raise TooManyStrays(len(strays))
    for name in strays:
        storage.delete(name)
    return len(strays)


class TooManyStrays(Exception):
    """More vault files than MAX_STRAY_FILES look stray at once; none was deleted."""
