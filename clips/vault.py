"""The vault: the only way in or out of what the archive holds for lawyers only (SPEC.md §6).

For now it holds a clip's context and contact details, as plaintext. Encryption
comes in M5 behind these same functions, so no caller has to change. Ingest (M2)
adds the evidence copy and proxy, and M6 adds messages.

Nothing here logs content. A new value replaces the old one in place, and an empty
value removes it: old values of context and contact details are never kept (SPEC.md §5).
"""

from .models import VaultText

CONTEXT = VaultText.Kind.CONTEXT
CONTACT_DETAILS = VaultText.Kind.CONTACT_DETAILS


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


def delete_all(clip):
    """Remove everything the vault holds for a clip."""
    VaultText.objects.filter(clip=clip).delete()
