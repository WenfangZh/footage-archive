"""Confirming an upload: the clip is created and its upload code issued (SPEC.md §5, §8)."""

from django.db import transaction
from django.views.decorators.debug import sensitive_variables

from . import tasks, upload_codes, vault
from .models import Clip, ClipTag, Original
from .time_blocks import four_hour_block


class AlreadyConfirmed(Exception):
    """The form for this original was confirmed already, by an earlier request."""


@sensitive_variables('code')
def confirm_upload(original, read, cleaned):
    """Create the clip for a confirmed form and return its upload code.

    `read` is the server's own quick read of the original: the metadata time and GPS
    come from it, never from the form. The code is returned once, to be shown on the
    code card. Only its keyed hash and its code holder public key are stored (ADR 0001).
    """
    code = upload_codes.generate()
    clip = Clip(
        code_hash=upload_codes.keyed_hash(code),
        # The texts below are sealed to it, for the code holder (docs/m5-encryption-design.md §7.2).
        code_holder_public_key=bytes(vault.code_holder_key(code).public_key),
        metadata_time=read.metadata_time,
        stated_time=cleaned['stated_time'],
        as_uploaded_stated_time=cleaned['stated_time'],
        as_uploaded_tags={'default': cleaned['tags'], 'custom': cleaned['custom_tags']},
    )
    if cleaned['latitude'] is not None:
        clip.set_stated_location(cleaned['latitude'], cleaned['longitude'], cleaned['precision'])
        clip.as_uploaded_stated_cell = clip.stated_cell
        clip.as_uploaded_stated_precision = clip.stated_precision
    if read.gps is not None:
        clip.set_metadata_location(*read.gps, int(cleaned['metadata_precision']))

    with transaction.atomic():
        clip.save()
        # Claim the original in one statement, so that of two submissions of the same
        # form, only one creates a clip; the other's work is rolled back.
        claimed = Original.objects.filter(pk=original.pk, clip__isnull=True).update(
            clip=clip, received_at=four_hour_block(original.received_at),
        )
        if not claimed:
            raise AlreadyConfirmed
        ClipTag.objects.bulk_create(
            [ClipTag(clip=clip, key=key, source=ClipTag.Source.CODE_HOLDER) for key in cleaned['tags']]
            + [ClipTag(clip=clip, custom_name=name, source=ClipTag.Source.CODE_HOLDER)
               for name in cleaned['custom_tags']]
        )
        vault.put_text(clip, vault.CONTEXT, cleaned['context'])
        vault.put_text(clip, vault.CONTACT_DETAILS, cleaned['contact_details'])
        # Only once committed, or the worker might look for a clip not there yet.
        transaction.on_commit(lambda: tasks.ingest_clip(clip.pk))
    return code
