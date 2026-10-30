"""What a code holder can do with a clip: change its details, or delete it (SPEC.md §5).

Each change records which field changed and when, never the values. The as-uploaded
values stay as they were, and old context and contact details are replaced in place.
"""

from django.db import transaction
from django.views.decorators.debug import sensitive_variables

from . import grid, upload_codes, vault
from .models import Clip, ClipTag, Edit, Original

Field = Edit.EditableField
# The clip's own fields a code holder edits: only these are saved, so that an edit never
# overwrites what ingest recorded meanwhile (its status, duration, size).
EDITED_FIELDS = ['stated_time', 'stated_cell', 'stated_precision', 'metadata_cell', 'code_holder_public_key']
# A failed clip has nothing left to describe (SPEC.md §9), and a withdrawn one no one will see again (§7).
EDITABLE = [Clip.Status.PROCESSING, Clip.Status.READY]


class NotEditable(Exception):
    """The clip failed, was withdrawn or was deleted after the request found it."""


def clip_for_code(typed):
    """The clip whose upload code was typed, or None."""
    code = upload_codes.normalise(typed or '')
    if code is None:
        return None
    return Clip.objects.filter(code_hash=upload_codes.keyed_hash(code)).first()


def code_holder_tags(clip):
    """The code holder's own tags. Lawyer tags are never shown to the code holder (SPEC.md §7)."""
    tags = ClipTag.objects.filter(clip=clip, source=ClipTag.Source.CODE_HOLDER)
    return (
        [tag.key for tag in tags if tag.key],
        [tag.custom_name for tag in tags if tag.custom_name],
    )


@sensitive_variables('cleaned', 'code', 'new_texts', 'text')
def apply_edits(clip, cleaned, code):
    """Apply the code holder's form to the clip; returns the fields that changed. `code` is the
    normalised upload code, which opens the clip's texts (docs/m5-encryption-design.md §7.2).

    Raises NotEditable if the clip is no longer EDITABLE, or was deleted, since it was loaded.
    """
    changed = []
    if clip.code_holder_public_key is None:
        # Confirmed before encryption: the key pair is known only now, from the code.
        clip.code_holder_public_key = bytes(vault.code_holder_key(code).public_key)

    if cleaned['stated_time'] != clip.stated_time:
        clip.stated_time = cleaned['stated_time']
        changed.append(Field.STATED_TIME)

    old_location = (clip.stated_cell, clip.stated_precision)
    if cleaned['latitude'] is None:
        clip.clear_stated_location()
    else:
        clip.set_stated_location(cleaned['latitude'], cleaned['longitude'], cleaned['precision'])
    if (clip.stated_cell, clip.stated_precision) != old_location:
        changed.append(Field.STATED_LOCATION)

    resolution = cleaned.get('metadata_precision')
    if resolution and int(resolution) < grid.resolution(clip.metadata_cell):
        clip.coarsen_metadata_location(int(resolution))
        changed.append(Field.METADATA_LOCATION)

    old_keys, old_custom = code_holder_tags(clip)
    new_tags = (cleaned['tags'], cleaned['custom_tags'])
    tags_changed = (set(old_keys), set(old_custom)) != (set(new_tags[0]), set(new_tags[1]))
    if tags_changed:
        changed.append(Field.TAGS)

    texts = {Field.CONTEXT: vault.CONTEXT, Field.CONTACT_DETAILS: vault.CONTACT_DETAILS}
    new_texts = {
        field: cleaned[field.value] for field, kind in texts.items()
        if cleaned[field.value] != vault.code_holder_text(clip, kind, code)
    }
    changed.extend(new_texts)

    with transaction.atomic():
        # Again, locked: ingest may have finished or given up, or lawyers withdrawn the clip, since
        # it was loaded.
        if not Clip.objects.select_for_update().filter(pk=clip.pk, status__in=EDITABLE).exists():
            raise NotEditable
        clip.save(update_fields=EDITED_FIELDS)
        if tags_changed:
            # Only the tags dropped or added change: a tag kept keeps its row, and with it what
            # lawyers did to it, such as removing it for lawyers (SPEC.md §7). A default tag's
            # custom name is empty, and a custom tag's key, so each exclude spares one kind.
            ClipTag.objects.filter(clip=clip, source=ClipTag.Source.CODE_HOLDER).exclude(
                key__in=new_tags[0],
            ).exclude(custom_name__in=new_tags[1]).delete()
            kept_keys, kept_custom = code_holder_tags(clip)
            ClipTag.objects.bulk_create(
                [ClipTag(clip=clip, key=key, source=ClipTag.Source.CODE_HOLDER)
                 for key in new_tags[0] if key not in kept_keys]
                + [ClipTag(clip=clip, custom_name=name, source=ClipTag.Source.CODE_HOLDER)
                   for name in new_tags[1] if name not in kept_custom]
            )
        for field, text in new_texts.items():
            vault.put_text(clip, texts[field], text)
        Edit.objects.bulk_create([Edit(clip=clip, field=field) for field in changed])
    # As stored, with what ingest recorded meanwhile, for the page that follows.
    clip.refresh_from_db()
    return changed


def delete_clip(clip):
    """Delete the clip, its tags, edits and vault contents, and its original if still there."""
    original = Original.objects.filter(clip=clip).first()
    with transaction.atomic():
        # Locked first, as the admin's deletion of a withdrawn clip does, so that the two never
        # deadlock, each holding rows the other waits for.
        Clip.objects.select_for_update().filter(pk=clip.pk).exists()
        vault.delete_all(clip)
        clip.delete()
        if original is not None:
            # Only once the database agrees, so that a failure leaves nothing half-deleted.
            transaction.on_commit(lambda: original.file.delete(save=False))

