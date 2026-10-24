"""The vault: the only way in or out of what the archive holds for lawyers only (SPEC.md §6).

It holds a clip's context and contact details, and its evidence copy and proxy. Each
file is encrypted with its own random data key, in libsodium's secretstream (ADR 0008):
the server seals the data key to the group keys when it records the file, and forgets
it. Reading a file takes its data key, which only a lawyer's browser can open and hand
back (docs/m5-encryption-design.md §6). The texts are still plaintext. M6 adds messages.

Nothing here logs content. A new value replaces the old one in place, and an empty
value removes it: old values of context and contact details are never kept (SPEC.md §5).

Files are stored under random names. A file is removed from disk only once the
database has let go of it, so a failure never leaves a row pointing at nothing; at
worst, if the process dies in between, it leaves a file no row points to, which
delete_stray_files removes once it is old enough.
"""

import datetime
import hmac
import logging
import math
import os
import re
import uuid
from pathlib import PurePath

from django.core.files import File
from django.db import transaction
from django.utils import timezone
from nacl import bindings as sodium
from nacl.encoding import RawEncoder
from nacl.exceptions import CryptoError
from nacl.hash import blake2b
from nacl.public import PublicKey, SealedBox
from nacl.utils import random

from keys import grants

from .models import SealedKey, VaultFile, VaultText, vault_storage

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

DATA_KEY_BYTES = 32
# The secretstream format (ADR 0008): a header, then chunks of CHUNK_SIZE bytes, each with
# ABYTES of overhead, the last one shorter or empty and marked final. Part of the format:
# sizes are computed from it.
CHUNK_SIZE = 64 * 1024
HEADER_BYTES = sodium.crypto_secretstream_xchacha20poly1305_HEADERBYTES
ABYTES = sodium.crypto_secretstream_xchacha20poly1305_ABYTES
FINAL = sodium.crypto_secretstream_xchacha20poly1305_TAG_FINAL
KEY_CHECK_LABEL = b'fa-keycheck-v1'


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


def store_file(clip, kind, source_path, extension):
    """Store an encrypted copy of the local file at `source_path`, as the clip's evidence copy
    or proxy to be; returns its name and its data key, which the caller holds in memory
    until it passes them to record_file.

    It touches no database row, so a caller can encrypt large files before opening its
    transaction, then record them with record_file inside it. Until it does, the file
    is unrecorded: delete_unrecorded_files removes it, and so would delete_stray_files
    once it is old enough.

    If the copy fails partway (a full disk, say), the partial file is removed again,
    so retries do not pile up copies of up to 2 GB.
    """
    storage = vault_storage()
    name = f'{uuid.uuid4().hex}.{extension}'
    data_key = random(DATA_KEY_BYTES)
    with open(source_path, 'rb') as source:
        content = _Encrypting(source, data_key, additional_data(clip, kind))
        try:
            return storage.save(name, content), data_key
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


def additional_data(clip, kind):
    """Bound to every chunk, so that a file moved to another clip, or swapped for the clip's
    other file, does not decrypt (ADR 0008)."""
    return f'fa-vaultfile-v1:{clip.pk}:{kind}'.encode()


class _Encrypting(File):
    """A file being encrypted into the vault, which notes whether the copying has begun."""

    started = False

    def __init__(self, file, data_key, ad):
        super().__init__(file)
        self.data_key = data_key
        self.ad = ad

    def chunks(self, chunk_size=None):
        self.started = True
        state = sodium.crypto_secretstream_xchacha20poly1305_state()
        yield sodium.crypto_secretstream_xchacha20poly1305_init_push(state, self.data_key)
        current = self.file.read(CHUNK_SIZE)
        while True:
            # One chunk ahead, so that the last one is marked final, even an empty one.
            following = self.file.read(CHUNK_SIZE)
            tag = FINAL if not following else sodium.crypto_secretstream_xchacha20poly1305_TAG_MESSAGE
            yield sodium.crypto_secretstream_xchacha20poly1305_push(state, current, self.ad, tag)
            if not following:
                return
            current = following


def key_check(data_key):
    return blake2b(KEY_CHECK_LABEL, digest_size=32, key=data_key, encoder=RawEncoder)


def record_file(clip, kind, name, data_key):
    """Make the stored file `name` the clip's evidence copy or proxy, replacing any old one,
    and seal its data key to every group key in use (design §5.5), under the group key lock."""
    with transaction.atomic():
        group_keys = grants.lock()
        if not group_keys:
            # Uploads are closed until a group key exists (design §10), and one is always in
            # use from then on: a file recorded now could never be read.
            raise NoGroupKey
        old = VaultFile.objects.select_for_update().filter(clip=clip, kind=kind).first()
        if old is not None:
            old.delete()
        vault_file = VaultFile.objects.create(clip=clip, kind=kind, file=name, key_check=key_check(data_key))
        for group_key in group_keys:
            SealedKey.objects.create(
                vault_file=vault_file, group_key=group_key,
                sealed_data_key=SealedBox(PublicKey(bytes(group_key.public_key))).encrypt(data_key),
            )
    if old is not None:
        storage = vault_storage()
        transaction.on_commit(lambda: storage.delete(old.file.name))


class NoGroupKey(Exception):
    """No group key is in use to seal a data key to."""


def encrypt_plaintext_files():
    """Encrypt each file stored before encryption (docs/m5-encryption-design.md §10), development
    data only; returns how many. Each is encrypted to a new name, then record_file switches its
    row over and deletes the plaintext once that commits: a crash leaves the old state or the
    new, and running it again skips what is already encrypted."""
    if not grants.in_use().exists():
        raise NoGroupKey
    storage = vault_storage()
    encrypted = 0
    for vault_file in VaultFile.objects.filter(key_check__isnull=True).select_related('clip'):
        suffix = PurePath(vault_file.file.name).suffix.lstrip('.')
        name, data_key = store_file(vault_file.clip, vault_file.kind, storage.path(vault_file.file.name), suffix)
        try:
            with transaction.atomic():
                if VaultFile.objects.select_for_update().filter(pk=vault_file.pk, key_check__isnull=True).exists():
                    record_file(vault_file.clip, vault_file.kind, name, data_key)
                    encrypted += 1
        finally:
            delete_unrecorded_files([name])
    return encrypted


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


def sealed_keys(clip, kind):
    """The data key of the clip's file, sealed to each group key in use, for a lawyer's browser
    to open with the one it holds (design §6.3)."""
    return SealedKey.objects.filter(
        vault_file__clip=clip, vault_file__kind=kind, group_key__status__in=grants.IN_USE,
    ).select_related('group_key')


def open_file(clip, kind, data_key):
    """The clip's evidence copy or proxy, decrypting as it is read; the caller closes it.

    Raises VaultFile.DoesNotExist or FileNotFoundError if there is no such file, and
    WrongKey if `data_key` (None for none) is not the file's.
    """
    vault_file = VaultFile.objects.get(clip=clip, kind=kind)
    if not _opens(vault_file, data_key):
        raise WrongKey
    return DecryptedFile(vault_file.file.open('rb'), data_key, additional_data(clip, kind), vault_file.file.name)


def is_data_key(clip, kind, data_key):
    """Whether `data_key` is the data key of the clip's evidence copy or proxy."""
    vault_file = VaultFile.objects.filter(clip=clip, kind=kind).first()
    return vault_file is not None and _opens(vault_file, data_key)


def _opens(vault_file, data_key):
    # In constant time (design §5.5). No key opens a file still plaintext (design §10).
    return (
        data_key is not None and vault_file.key_check is not None
        and hmac.compare_digest(key_check(data_key), bytes(vault_file.key_check))
    )


class WrongKey(Exception):
    """A data key that is not the file's."""


class Unreadable(Exception):
    """A vault file that failed to decrypt: cut off, altered, or moved from another clip or
    kind. Raised as it is read, so a response serving it is cut off, never completed."""


def plaintext_size(stored_size):
    """The size a file decrypts to, from the size stored: every chunk but the last is full."""
    body = stored_size - HEADER_BYTES
    chunks = max(1, math.ceil(body / (CHUNK_SIZE + ABYTES)))
    last = body - (chunks - 1) * (CHUNK_SIZE + ABYTES)
    if last < ABYTES:
        return None  # No file this format writes: cut off, or something else.
    return body - chunks * ABYTES


class DecryptedFile:
    """A read-only, seekable binary file over a vault file, decrypting as it is read.

    Seeking starts the decryption again from the beginning, since secretstream decrypts only
    in order (ADR 0008), and skips to the position. `size` is the plaintext size.
    """

    def __init__(self, stored, data_key, ad, name):
        self._stored = stored
        self._data_key = data_key
        self._ad = ad
        self._name = name
        self._stored_size = os.fstat(stored.fileno()).st_size
        self.size = plaintext_size(self._stored_size)
        try:
            if self.size is None:
                self._fail()
            self.seek(0)
        except Unreadable:
            stored.close()
            raise

    def seek(self, position):
        self._stored.seek(0)
        self._state = sodium.crypto_secretstream_xchacha20poly1305_state()
        try:
            sodium.crypto_secretstream_xchacha20poly1305_init_pull(self._state, self._stored.read(HEADER_BYTES), self._data_key)
        except (CryptoError, ValueError):
            self._fail()
        self._buffer = b''
        self._finished = False
        while position > 0:
            chunk = self._next_chunk()
            if not chunk:
                break
            if len(chunk) > position:
                self._buffer = chunk[position:]
                break
            position -= len(chunk)

    def read(self, size=-1):
        parts = [self._buffer]
        length = len(self._buffer)
        while size < 0 or length < size:
            chunk = self._next_chunk()
            if not chunk:
                break
            parts.append(chunk)
            length += len(chunk)
        data = b''.join(parts)
        if size < 0:
            self._buffer = b''
            return data
        self._buffer = data[size:]
        return data[:size]

    def _next_chunk(self):
        """The next chunk's plaintext, or b'' after the final one."""
        if self._finished:
            return b''
        block = self._stored.read(CHUNK_SIZE + ABYTES)
        try:
            chunk, tag = sodium.crypto_secretstream_xchacha20poly1305_pull(self._state, block, self._ad)
        except (CryptoError, ValueError):
            # Includes a file that ends without its final chunk.
            self._fail()
        # The final chunk, and only it, ends the file: checked before its bytes are returned, so
        # that a file cut at a chunk boundary fails even when a reader stops at `size` and
        # never asks past it, and anything after the final chunk fails too.
        self._finished = tag == FINAL
        if self._finished != (self._stored.tell() >= self._stored_size):
            self._fail()
        return chunk

    def _fail(self):
        # The name is random: safe to log.
        logger.error('The vault file %s did not decrypt', self._name)
        raise Unreadable(self._name)

    def close(self):
        self._stored.close()

    @property
    def closed(self):
        return self._stored.closed

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


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
