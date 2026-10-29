"""What a lawyer does to a clip's tags from its page (SPEC.md §7, cases.lawyer_tags): add a tag,
propose removing or restoring one, or cancel their own proposal. Each goes through one of the
lawyer's own usable cases that the clip matches, as the page itself does.
"""

from django import forms
from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_GET, require_POST

from accounts.roles import lawyer_required
from clips.models import ClipTag
from clips.tags import CUSTOM_TAG_MAX_LENGTH, DEFAULT_TAGS

from . import lawyer_tags
from .clip_pages import case_and_clip


class LawyerTagForm(forms.Form):
    """One tag: a default tag, or a custom one."""

    key = forms.ChoiceField(
        label=gettext_lazy('Default tag'), required=False, choices=[('', '—'), *DEFAULT_TAGS.items()],
    )
    custom_name = forms.CharField(
        label=gettext_lazy('Or your own tag'), required=False, max_length=CUSTOM_TAG_MAX_LENGTH,
        help_text=gettext_lazy(
            'Every lawyer whose case finds this clip will see it. Write only what can be seen, nothing about your case.'
        ),
    )

    def clean(self):
        cleaned = super().clean()
        if bool(cleaned.get('key')) == bool((cleaned.get('custom_name') or '').strip()):
            raise forms.ValidationError(_('Choose a default tag or write your own, not both.'))
        return cleaned


def back_to_clip(case, clip):
    return redirect('lawyer:clip', case_id=case.pk, clip_id=clip.pk)


@lawyer_required
@require_POST
def add_tag(request, case_id, clip_id):
    case, clip, _match = case_and_clip(request, case_id, clip_id)
    form = LawyerTagForm(request.POST)
    if not form.is_valid():
        messages.error(request, ' '.join(error for errors in form.errors.values() for error in errors))
        return back_to_clip(case, clip)
    try:
        lawyer_tags.add(case, clip, form.cleaned_data['key'], form.cleaned_data['custom_name'])
    except lawyer_tags.Refused:
        messages.error(request, _('This clip already has this tag, or lawyers have removed it: propose restoring it instead.'))
    return back_to_clip(case, clip)


@lawyer_required
@require_POST
def propose(request, case_id, clip_id, tag_id):
    case, clip, _match = case_and_clip(request, case_id, clip_id)
    tag = get_object_or_404(ClipTag, pk=tag_id, clip=clip)
    try:
        lawyer_tags.propose(case, clip, tag)
    except lawyer_tags.Refused:
        messages.error(request, _('You have already made a proposal on this tag.'))
    return back_to_clip(case, clip)


@lawyer_required
@require_POST
def cancel(request, case_id, clip_id, tag_id):
    case, clip, _match = case_and_clip(request, case_id, clip_id)
    tag = get_object_or_404(ClipTag, pk=tag_id, clip=clip)
    try:
        lawyer_tags.cancel(case, clip, tag)
    except lawyer_tags.Refused:
        messages.error(request, _('You have no open proposal on this tag.'))
    return back_to_clip(case, clip)


@lawyer_required
@require_GET
def tag_colours(request):
    """The colours of each tag's source, from the settings (checked there as #rrggbb)."""
    rules = '\n'.join(
        f'.tag-source-{source} {{ border-left: 0.5em solid {colour}; padding-left: 0.4em; }}'
        for source, colour in [
            (ClipTag.Source.CODE_HOLDER, settings.TAG_COLOUR_CODE_HOLDER),
            (ClipTag.Source.LAWYER, settings.TAG_COLOUR_LAWYER),
        ]
    )
    return HttpResponse(rules + '\n', content_type='text/css; charset=utf-8')
