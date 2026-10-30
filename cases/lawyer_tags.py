"""Lawyers' tags (SPEC.md §7): adding a tag to a clip, and removing or restoring a tag by three
proposals from three different lawyers.

Each function takes the lawyer's usable case that matches the clip, which the view checks
(cases.clip_pages.case_and_clip), and writes an audit entry naming the tag by its id, never its
text. Removal is for lawyers only: the code holder's own view and the as-uploaded values are
untouched (clips.editing keeps a kept tag's row, and with it its state).
"""

import unicodedata

from django.db import transaction
from django.utils import translation

from accounts.models import LawyerProfile
from audit.models import Action, record
from clips.models import Clip, ClipTag, TagProposal
from clips.tags import DEFAULT_TAGS

# Proposals from this many different lawyers remove a tag, or restore a removed one.
PROPOSALS_NEEDED = 3


class Refused(Exception):
    """The tag is already there, or the proposal already made or not open."""


class Changed(Refused):
    """The tag was removed or restored since the lawyer chose which way to propose."""


class Gone(Exception):
    """The tag or the clip was deleted, or the clip is no longer ready, since the request found it."""


def _names_of_default_tags():
    """Each default tag's key, by its name in French and in English, compared as `_same`."""
    names = {}
    for language in ('fr', 'en'):
        with translation.override(language):
            names.update({_same(str(name)): key for key, name in DEFAULT_TAGS.items()})
    return names


def _same(name):
    # Composed before and after casefold, so that the same text typed or pasted another way,
    # including a code holder's custom tag stored as typed, compares alike.
    return unicodedata.normalize('NFC', unicodedata.normalize('NFC', name.strip()).casefold())


def identity(key, custom_name, default_names):
    """What makes two tags the same (SPEC.md §7): the same default tag, or the same custom text
    ignoring case and surrounding spaces; a custom tag named like a default tag is that tag.
    `default_names` is _names_of_default_tags(), made once by the caller."""
    if key:
        return ('default', key)
    named = default_names.get(_same(custom_name))
    return ('default', named) if named else ('custom', _same(custom_name))


def _locked(queryset, **lookup):
    try:
        return queryset.select_for_update().get(**lookup)
    except queryset.model.DoesNotExist:
        raise Gone from None


def add(case, clip, key='', custom_name=''):
    """Add a lawyer's tag to the clip, unless it already has the same one, shown or removed."""
    default_names = _names_of_default_tags()
    wanted = identity(key, custom_name, default_names)
    with transaction.atomic():
        # Locked, so that two lawyers adding the same tag at once add it once.
        _locked(Clip.objects, pk=clip.pk, status=Clip.Status.READY)
        if any(identity(tag.key, tag.custom_name, default_names) == wanted for tag in clip.tags.all()):
            raise Refused
        kind, value = wanted
        tag = ClipTag.objects.create(
            clip=clip, source=ClipTag.Source.LAWYER,
            **({'key': value} if kind == 'default' else {'custom_name': custom_name.strip()}),
        )
        record(Action.TAG_ADDITION, case.lawyer_profile, case=case, clip=clip, clip_tag=tag)
    return tag


def propose(case, clip, tag, restore):
    """Propose removing the tag, or restoring it if `restore`; refused (Changed) if the tag is not
    in the state that undoes. The proposal that brings those of admitted, unsuspended lawyers to
    PROPOSALS_NEEDED removes or restores it, and closes them all."""
    profile = case.lawyer_profile
    with transaction.atomic():
        tag = _locked(ClipTag.objects, pk=tag.pk, clip=clip, clip__status=Clip.Status.READY)
        if tag.removed != restore:
            raise Changed
        if tag.proposals.filter(lawyer_profile=profile).exists():
            raise Refused
        TagProposal.objects.create(tag=tag, lawyer_profile=profile)
        proposal = Action.TAG_RESTORATION_PROPOSAL if tag.removed else Action.TAG_REMOVAL_PROPOSAL
        record(proposal, profile, case=case, clip=clip, clip_tag=tag)
        if tag.proposals.filter(lawyer_profile__status=LawyerProfile.Status.LAWYER).count() >= PROPOSALS_NEEDED:
            tag.removed = not tag.removed
            tag.save(update_fields=['removed'])
            # The audit log keeps who proposed; counting starts again (SPEC.md §7).
            tag.proposals.all().delete()
            record(Action.TAG_REMOVAL if tag.removed else Action.TAG_RESTORATION, profile, case=case, clip=clip, clip_tag=tag)
    return tag


def cancel(case, clip, tag):
    """Cancel the lawyer's own open proposal on the tag."""
    profile = case.lawyer_profile
    with transaction.atomic():
        tag = _locked(ClipTag.objects, pk=tag.pk, clip=clip, clip__status=Clip.Status.READY)
        deleted, _ = tag.proposals.filter(lawyer_profile=profile).delete()
        if not deleted:
            raise Refused
        record(Action.TAG_PROPOSAL_CANCELLING, profile, case=case, clip=clip, clip_tag=tag)
