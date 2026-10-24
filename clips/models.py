import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from . import grid
from .tags import CUSTOM_TAG_MAX_LENGTH, DEFAULT_TAGS
from .time_blocks import four_hour_block


class FourHourBlockField(models.DateTimeField):
    """A date and time stored only to its 4-hour block (SPEC.md §6)."""

    def pre_save(self, model_instance, add):
        value = super().pre_save(model_instance, add)
        if value is not None:
            value = four_hour_block(value)
            setattr(model_instance, self.attname, value)
        return value


class Precision(models.TextChoices):
    """How precisely the uploader or code holder knows the stated location."""

    EXACT_SPOT = 'exact_spot', _('Exact spot')
    STREET = 'street', _('Street')
    NEIGHBOURHOOD = 'neighbourhood', _('Neighbourhood')


# The H3 resolution of the cell kept for each stated precision (ADR 0004).
STATED_RESOLUTIONS = {
    Precision.EXACT_SPOT: 11,
    Precision.STREET: 10,
    Precision.NEIGHBOURHOOD: 9,
}

# The resolutions the uploader may choose for the metadata location (ADR 0004).
METADATA_RESOLUTIONS = (11, 10, 9)


class Clip(models.Model):
    """One video in the archive, with its times, locations and tags (GLOSSARY.md)."""

    class Status(models.TextChoices):
        # Waiting for ingest, or in it (SPEC.md §9).
        PROCESSING = 'processing', _('Processing')
        # Ingest finished: the clip has its evidence copy, proxy and hash record.
        READY = 'ready', _('Ready')
        # Ingest failed for good: the original is deleted, and the code holder can upload again.
        FAILED = 'failed', _('Failed')

    # Random, so that ids say nothing about upload order.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # HMAC-SHA-256 of the upload code (clips.upload_codes); the code itself is never stored.
    code_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROCESSING)
    upload_time = FourHourBlockField(default=timezone.now)

    # Read from the file. Never modified.
    metadata_time = models.DateTimeField(null=True, blank=True)
    # Read from the file at ingest. Matching uses the duration (SPEC.md §7); the size is
    # the picture as it displays, so a portrait video is taller than wide.
    duration = models.DurationField(null=True, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    # An H3 cell; the code holder can make it coarser, never finer.
    metadata_cell = models.BigIntegerField(null=True, blank=True)

    stated_time = models.DateTimeField(null=True, blank=True)
    # An H3 cell whose resolution follows the stated precision.
    stated_cell = models.BigIntegerField(null=True, blank=True)
    stated_precision = models.CharField(max_length=20, choices=Precision.choices, blank=True)

    # The stated values and tags as they were at upload. Never modified.
    as_uploaded_stated_time = models.DateTimeField(null=True, blank=True)
    as_uploaded_stated_cell = models.BigIntegerField(null=True, blank=True)
    as_uploaded_stated_precision = models.CharField(max_length=20, choices=Precision.choices, blank=True)
    # {"default": [tag keys], "custom": [custom tag names]}
    as_uploaded_tags = models.JSONField(default=dict)

    IMMUTABLE_FIELDS = (
        'code_hash', 'upload_time', 'metadata_time', 'as_uploaded_stated_time',
        'as_uploaded_stated_cell', 'as_uploaded_stated_precision', 'as_uploaded_tags',
    )

    class Meta:
        constraints = [
            # A stated location is a cell and a precision, or nothing.
            models.CheckConstraint(
                condition=Q(stated_cell__isnull=True, stated_precision='')
                | (Q(stated_cell__isnull=False) & ~Q(stated_precision='')),
                name='stated_location_has_cell_and_precision',
            ),
            models.CheckConstraint(
                condition=Q(as_uploaded_stated_cell__isnull=True, as_uploaded_stated_precision='')
                | (Q(as_uploaded_stated_cell__isnull=False) & ~Q(as_uploaded_stated_precision='')),
                name='as_uploaded_location_has_cell_and_precision',
            ),
        ]

    def set_stated_location(self, latitude, longitude, precision):
        """Keep only the cell of the given point that matches its precision."""
        self.stated_cell = grid.cell_for_point(latitude, longitude, STATED_RESOLUTIONS[precision])
        self.stated_precision = precision

    def clear_stated_location(self):
        self.stated_cell = None
        self.stated_precision = ''

    def set_metadata_location(self, latitude, longitude, resolution):
        if resolution not in METADATA_RESOLUTIONS:
            raise ValueError(f'Metadata location resolution must be one of {METADATA_RESOLUTIONS}.')
        self.metadata_cell = grid.cell_for_point(latitude, longitude, resolution)

    def coarsen_metadata_location(self, resolution):
        if resolution not in METADATA_RESOLUTIONS:
            raise ValueError(f'Metadata location resolution must be one of {METADATA_RESOLUTIONS}.')
        self.metadata_cell = grid.coarsen(self.metadata_cell, resolution)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            self._refuse_forbidden_changes()
        super().save(*args, **kwargs)

    def _refuse_forbidden_changes(self):
        stored = Clip.objects.filter(pk=self.pk).values(*self.IMMUTABLE_FIELDS, 'metadata_cell').first()
        if stored is None:
            return
        # Compare as stored: the upload time is kept only to its block.
        if self.upload_time is not None:
            self.upload_time = four_hour_block(self.upload_time)
        for field in self.IMMUTABLE_FIELDS:
            if getattr(self, field) != stored[field]:
                raise ValueError(f'{field} cannot be changed after upload.')
        old_cell, new_cell = stored['metadata_cell'], self.metadata_cell
        if new_cell != old_cell and not (
            old_cell is not None and new_cell is not None
            and grid.resolution(new_cell) < grid.resolution(old_cell)
            and grid.coarsen(old_cell, grid.resolution(new_cell)) == new_cell
        ):
            raise ValueError('The metadata location can only be made coarser.')


class ClipTag(models.Model):
    """A tag on a clip: a default tag (by key) or a custom tag (as typed)."""

    class Source(models.TextChoices):
        CODE_HOLDER = 'code_holder', _('code holder')
        LAWYER = 'lawyer', _('lawyer')

    clip = models.ForeignKey(Clip, on_delete=models.CASCADE, related_name='tags')
    key = models.CharField(max_length=40, choices=list(DEFAULT_TAGS.items()), blank=True)
    custom_name = models.CharField(max_length=CUSTOM_TAG_MAX_LENGTH, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(~Q(key='') & Q(custom_name='')) | (Q(key='') & ~Q(custom_name='')),
                name='tag_is_default_or_custom',
            ),
            models.UniqueConstraint(
                fields=['clip', 'source', 'key'], condition=~Q(key=''), name='unique_default_tag',
            ),
            models.UniqueConstraint(
                fields=['clip', 'source', 'custom_name'], condition=~Q(custom_name=''),
                name='unique_custom_tag',
            ),
        ]


class Edit(models.Model):
    """A change the code holder made after upload: which field, and when. Never the values."""

    class EditableField(models.TextChoices):
        STATED_TIME = 'stated_time', _('Stated time')
        STATED_LOCATION = 'stated_location', _('Stated location')
        TAGS = 'tags', _('Tags')
        CONTEXT = 'context', _('Context')
        CONTACT_DETAILS = 'contact_details', _('Contact details')
        METADATA_LOCATION = 'metadata_location', _('Metadata location')

    clip = models.ForeignKey(Clip, on_delete=models.CASCADE, related_name='edits')
    field = models.CharField(max_length=30, choices=EditableField.choices)
    edited_at = FourHourBlockField(default=timezone.now)


class HashRecord(models.Model):
    """The fingerprints of a clip's original and evidence copy (SPEC.md §9, clips.hashing).

    It outlives the clip: deleting the clip only removes the link. Stream hashes are
    lists of [kind, hex digest], kind 'v' or 'a', in stream order. Times are kept
    only to their 4-hour block, like the upload time: hashing runs minutes after
    upload, so an exact time would give the upload time away (SPEC.md §6).
    Once written, a hash record never changes.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clip = models.OneToOneField(
        Clip, null=True, blank=True, on_delete=models.SET_NULL, related_name='hash_record',
    )
    original_sha256 = models.CharField(max_length=64)
    original_stream_hashes = models.JSONField()
    original_hashed_at = FourHourBlockField()
    evidence_sha256 = models.CharField(max_length=64)
    evidence_stream_hashes = models.JSONField()
    evidence_hashed_at = FourHourBlockField()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            # Only the link to a deleted clip may go, which the database does itself.
            raise ValueError('A hash record cannot be changed.')
        super().save(*args, **kwargs)


class VaultText(models.Model):
    """Text held in the vault for a clip. Only clips.vault reads or writes it (SPEC.md §6).

    Plaintext until M5. It deliberately has no readable __str__, so that its content
    never ends up in logs or error pages through a model's text form.
    """

    class Kind(models.TextChoices):
        CONTEXT = 'context', _('Context')
        CONTACT_DETAILS = 'contact_details', _('Contact details')

    clip = models.ForeignKey(Clip, on_delete=models.CASCADE, related_name='+')
    kind = models.CharField(max_length=20, choices=Kind.choices)
    content = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['clip', 'kind'], name='one_vault_text_per_kind'),
        ]


class VaultStorage(FileSystemStorage):
    """The vault's folder on disk. Unlike Django's, it never picks another name on a clash.

    Names are random, so a clash is a bug. Picking another name would also hide which
    file a failed save wrote, and clips.vault must remove only what it created.
    """

    def get_available_name(self, name, max_length=None):
        if super().get_available_name(name, max_length) != name:
            raise FileExistsError(name)
        return name


def vault_storage():
    # Never served over the web: nothing routes to this folder (SPEC.md §4).
    return VaultStorage(location=settings.VAULT_DIR)


class VaultFile(models.Model):
    """A file held in the vault for a clip. Only clips.vault reads or writes it (SPEC.md §6).

    Encrypted with its own data key (ADR 0008), which is stored only sealed to the group
    keys (SealedKey, below). A clip cannot be deleted while it still has vault files,
    so that no file is left behind on disk: clips.vault.delete_all removes them first.
    """

    class Kind(models.TextChoices):
        EVIDENCE_COPY = 'evidence_copy', _('Evidence copy')
        PROXY = 'proxy', _('Proxy')

    clip = models.ForeignKey(Clip, on_delete=models.PROTECT, related_name='+')
    kind = models.CharField(max_length=20, choices=Kind.choices)
    file = models.FileField(storage=vault_storage)
    # BLAKE2b keyed with the data key, so that a key someone presents can be checked
    # without the file (design §5.5). Empty only on a file stored before M5, still plaintext,
    # until `encrypt_vault` encrypts it (design §10).
    key_check = models.BinaryField(max_length=32, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['clip', 'kind'], name='one_vault_file_per_kind'),
        ]


# crypto_box_seal of a 32-byte data key: a 32-byte ephemeral public key, the key and a 16-byte tag.
SEALED_DATA_KEY_BYTES = 32 + 32 + 16


class SealedKey(models.Model):
    """A vault item's data key, sealed to a group key (docs/m5-encryption-design.md §3.1, §3.2):
    what a lawyer holding that group key opens to read the item. The server seals it when it
    records the item, and forgets the data key. Only clips.vault reads or writes it."""

    vault_file = models.ForeignKey(VaultFile, on_delete=models.CASCADE, related_name='sealed_keys')
    group_key = models.ForeignKey('keys.GroupKey', on_delete=models.PROTECT, related_name='sealed_keys')
    sealed_data_key = models.BinaryField(max_length=SEALED_DATA_KEY_BYTES)
    # A rotator who resealed it (design §5.5); empty when the server sealed it.
    sealed_by = models.ForeignKey(
        'accounts.LawyerProfile', null=True, blank=True, on_delete=models.PROTECT, related_name='+',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['vault_file', 'group_key'], name='one_sealed_key_per_item_and_group_key'),
        ]


def quarantine_storage():
    # Never served over the web: nothing routes to this folder (SPEC.md §8).
    return FileSystemStorage(location=settings.QUARANTINE_DIR)


def random_file_name(instance, filename):
    # The uploader's file name is dropped: it is uploader-supplied and can identify them.
    return uuid.uuid4().hex


class Original(models.Model):
    """A clip's file exactly as received, waiting in quarantine (GLOSSARY.md).

    It exists before its clip does: the clip is created when the uploader confirms
    the form, and an original never confirmed is deleted after
    UNCONFIRMED_ORIGINAL_MINUTES.
    """

    # Random: it also links upload step 1 to step 2.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.FileField(storage=quarantine_storage, upload_to=random_file_name)
    # Precise while unconfirmed, for the deadline; kept only to its block once confirmed.
    received_at = models.DateTimeField(default=timezone.now)
    clip = models.OneToOneField(
        Clip, null=True, blank=True, on_delete=models.CASCADE, related_name='original',
    )

    def save(self, *args, **kwargs):
        if self.clip_id is not None:
            self.received_at = four_hour_block(self.received_at)
        super().save(*args, **kwargs)
