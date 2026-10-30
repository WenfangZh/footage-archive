"""What a lawyer does to a clip's tags from its page (SPEC.md §7, cases.lawyer_tags): add a tag,
propose removing or restoring one, or cancel their own proposal. Each goes through one of the
lawyer's own usable cases that the clip matches, as the page itself does.
"""

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from accounts.roles import lawyer_required
from clips.models import ClipTag

from . import lawyer_tags
from .clip_pages import case_and_clip
from .forms import LawyerTagForm


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
    except lawyer_tags.Gone:
        raise Http404 from None
    return back_to_clip(case, clip)


@lawyer_required
@require_POST
def propose(request, case_id, clip_id, tag_id):
    case, clip, _match = case_and_clip(request, case_id, clip_id)
    tag = get_object_or_404(ClipTag, pk=tag_id, clip=clip)
    # What the lawyer clicked, so that a page shown before the tag changed casts no vote the
    # other way.
    restore = {'remove': False, 'restore': True}.get(request.POST.get('direction'))
    if restore is None:
        return HttpResponse(status=400)
    try:
        lawyer_tags.propose(case, clip, tag, restore)
    except lawyer_tags.Changed:
        messages.error(request, _('Lawyers have removed or restored this tag since the page was shown: look again.'))
    except lawyer_tags.Refused:
        messages.error(request, _('You have already made a proposal on this tag.'))
    except lawyer_tags.Gone:
        raise Http404 from None
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
    except lawyer_tags.Gone:
        raise Http404 from None
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
