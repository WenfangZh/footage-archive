import datetime

from django.conf import settings
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_http_methods

from keys import grants
from keys.grants import uploads_open

from . import grid, probe, rate_limits, vault
from .confirmation import AlreadyConfirmed, confirm_upload
from .editing import EDITABLE, NotEditable, apply_edits, clip_for_code, code_holder_tags, delete_clip
from .forms import MESSAGE_MAX_LENGTH, METADATA_PRECISIONS, ConfirmForm, EditForm, FileForm, ReplyForm
from .models import Clip, Message, Original, Precision
from .tags import DEFAULT_TAGS
from .threads import code_holder_threads
from .upload_codes import display, normalise
from .uploads import size_limit_display

# How the metadata location's cell size is described, by H3 resolution.
METADATA_SIZES = {int(value): label for value, label in METADATA_PRECISIONS}

# Fields that error reports must never show (SPEC.md §10).
PRIVATE_FIELDS = ('latitude', 'longitude', 'custom_tags', 'context', 'contact_details')


@require_http_methods(['GET', 'POST'])
def upload(request):
    """Upload step 1: receive the file into quarantine and check that it is a video."""
    if not uploads_open():
        return uploads_closed(request)
    if request.method == 'GET':
        return render_upload(request, FileForm())

    files = request.FILES  # Reading the files runs the upload handlers.
    if getattr(request, 'upload_too_large', False):
        return render_upload(request, FileForm(), problem='too_large', status=413)
    form = FileForm(request.POST, files)
    if not form.is_valid():
        return render_upload(request, form, status=400)

    received = form.cleaned_data['file']
    try:
        probe.quick_read(received.temporary_file_path())
    except probe.NotAVideo:
        received.close()  # Deletes the temporary file: nothing is kept.
        return render_upload(request, FileForm(), problem='not_a_video', status=400)

    original = Original()
    # Moves the file within quarantine, under a random name (clips.models.random_file_name).
    original.file.save('original', received)
    return redirect('clips:confirm', original_id=original.id)


def uploads_closed(request):
    """Refused while no lawyer could read what is uploaded (docs/m5-encryption-design.md §5.7, §10):
    ingest never has to store a file it cannot encrypt for someone."""
    return render(request, 'clips/uploads_closed.html', status=503)


def render_upload(request, form, problem=None, status=200):
    return render(request, 'clips/upload.html', {
        'form': form,
        'problem': problem,
        'size_limit': size_limit_display(),
        'partial_upload_minutes': settings.PARTIAL_UPLOAD_MINUTES,
    }, status=status)


@never_cache
@sensitive_post_parameters(*PRIVATE_FIELDS)
@sensitive_variables('code')
@require_http_methods(['GET', 'POST'])
def confirm(request, original_id):
    """Upload step 2: the form, then the code card. The metadata time is never a field."""
    if Original.objects.filter(id=original_id, clip__isnull=False).exists():
        return already_sent(request)
    original = unconfirmed_original_or_404(original_id)
    if not uploads_open():
        return uploads_closed(request)
    read = probe.quick_read(original.file.path)
    has_gps = read.gps is not None

    if request.method == 'GET':
        form = ConfirmForm(initial=initial_values(read), has_gps=has_gps)
    else:
        form = ConfirmForm(request.POST, has_gps=has_gps)
        if form.is_valid():
            try:
                code = confirm_upload(original, read, form.cleaned_data)
            except AlreadyConfirmed:
                return already_sent(request)
            return render(request, 'clips/code_card.html', {
                'code': display(code),
                'card_label': card_label_suggestion(form.cleaned_data),
                'missing_time_or_place': (
                    form.cleaned_data['stated_time'] is None or form.cleaned_data['latitude'] is None
                ),
                'return_url': request.build_absolute_uri(reverse('clips:code_holder')),
            })
    return render(request, 'clips/confirm.html', {'form': form, 'read': read}, status=400 if form.errors else 200)


def unconfirmed_original_or_404(original_id):
    deadline = timezone.now() - datetime.timedelta(minutes=settings.UNCONFIRMED_ORIGINAL_MINUTES)
    original = Original.objects.filter(
        id=original_id, clip__isnull=True, received_at__gt=deadline,
    ).first()
    if original is None:
        raise Http404
    return original


def already_sent(request):
    return render(request, 'clips/already_sent.html', status=410)


def initial_values(read):
    """Pre-fill the form from the quick read: the metadata time and the exact GPS position."""
    initial = {}
    if read.metadata_time is not None:
        initial['stated_time'] = read.metadata_time
    if read.gps is not None:
        initial['latitude'], initial['longitude'] = (round(value, 6) for value in read.gps)
        initial['precision'] = Precision.EXACT_SPOT
    return initial


@never_cache
@sensitive_post_parameters('code')
@sensitive_variables('typed')
@require_http_methods(['GET', 'POST'])
def code_holder(request):
    """Coming back with an upload code. The code is never put in a URL or a session."""
    if request.method == 'GET':
        return render(request, 'clips/code_entry.html')
    typed = request.POST.get('code', '')
    clip, refusal = clip_or_refusal(request, typed)
    if refusal:
        return refusal
    return render_clip(request, clip, edit_form(clip, code=typed))


@never_cache
@sensitive_post_parameters('code', *PRIVATE_FIELDS)
@sensitive_variables('typed')
@require_http_methods(['POST'])
def edit(request):
    typed = request.POST.get('code', '')
    clip, refusal = clip_or_refusal(request, typed)
    if refusal:
        return refusal
    if clip.status not in EDITABLE:
        return render_clip(request, clip, edit_form(clip, code=typed), status=409)
    form = edit_form(clip, data=request.POST)
    if not form.is_valid():
        return render_clip(request, clip, form, status=400)
    try:
        changed = apply_edits(clip, form.cleaned_data, normalise(typed))
    except NotEditable:
        # Failed, withdrawn or deleted meanwhile: shown as it is now.
        clip = clip_for_code(typed)
        if clip is None:
            return render(request, 'clips/code_entry.html', {'wrong_code': True}, status=404)
        return render_clip(request, clip, edit_form(clip, code=typed), status=409)
    return render_clip(request, clip, edit_form(clip, code=typed), saved=bool(changed))


@never_cache
@sensitive_post_parameters('code')
@sensitive_variables('typed')
@require_http_methods(['POST'])
def delete(request):
    typed = request.POST.get('code', '')
    clip, refusal = clip_or_refusal(request, typed)
    if refusal:
        return refusal
    if request.POST.get('confirm_delete') != 'yes':
        return render_clip(request, clip, edit_form(clip, code=typed), delete_unconfirmed=True, status=400)
    delete_clip(clip)
    return render(request, 'clips/deleted.html')


@never_cache
@sensitive_post_parameters('code', 'text')
@sensitive_variables('typed')
@require_http_methods(['POST'])
def reply(request):
    """The code holder answers a lawyer's thread (SPEC.md §7). Refused, with the reply kept in its
    box, while the lawyer cannot receive it: no longer admitted, suspended, or without a personal
    key the admin confirmed; the notice for the last says only to try again later."""
    typed = request.POST.get('code', '')
    clip, refusal = clip_or_refusal(request, typed)
    if refusal:
        return refusal
    form = ReplyForm(request.POST)
    status = 200
    with transaction.atomic():
        # Under the lock that deleting expired threads takes, so that the thread is still there
        # when the reply joins it, rather than the reply starting a thread of its own.
        grants.lock()
        first = form.is_valid() and Message.objects.filter(
            clip=clip, pk=form.cleaned_data['thread'],
        ).select_related('lawyer_profile').first()
        if not first:
            status = 400
        else:
            try:
                vault.send_message(clip, first.lawyer_profile, Message.Direction.FROM_CODE_HOLDER, form.cleaned_data['text'])
            except (vault.NotALawyer, vault.NoLawyerKey):
                # Its lawyer changed since the page was shown.
                status = 409
    if status != 200:
        return render_clip(request, clip, edit_form(clip, code=typed), reply_failed=form, status=status)
    return render_clip(request, clip, edit_form(clip, code=typed), replied=True)


def clip_or_refusal(request, typed):
    """The clip for the code sent, or the response refusing it (SPEC.md §5).

    Once a connection has had too many wrong codes, no code is checked at all, not
    even a right one, so that guessing cannot go on.
    """
    if rate_limits.is_limited(request, rate_limits.FAILED_CODES):
        return None, rate_limits.too_many(request)
    clip = clip_for_code(typed)
    if clip is None:
        rate_limits.count(request, rate_limits.FAILED_CODES)
        # The same answer whether the code is mistyped or unknown.
        return None, render(request, 'clips/code_entry.html', {'wrong_code': True}, status=404)
    return clip, None


@sensitive_variables('code', 'initial')
def edit_form(clip, code=None, data=None):
    """The edit form for a clip: pre-filled from it, or bound to what was sent."""
    stated_centre = None
    if clip.stated_cell is not None:
        stated_centre = tuple(round(value, 6) for value in grid.centre(clip.stated_cell))
    kwargs = {
        'stated_centre': stated_centre,
        'stated_resolution': grid.resolution(clip.stated_cell) if clip.stated_cell else None,
        'metadata_resolution': grid.resolution(clip.metadata_cell) if clip.metadata_cell else None,
    }
    if data is not None:
        return EditForm(data, **kwargs)
    keys, custom = code_holder_tags(clip)
    initial = {
        'code': code,
        'stated_time': clip.stated_time,
        'precision': clip.stated_precision,
        'tags': keys,
        'custom_tags': ', '.join(custom),
        # Opened with the code, during this request only (docs/m5-encryption-design.md §7.2).
        'context': vault.code_holder_text(clip, vault.CONTEXT, normalise(code)),
        'contact_details': vault.code_holder_text(clip, vault.CONTACT_DETAILS, normalise(code)),
    }
    if stated_centre is not None:
        initial['latitude'], initial['longitude'] = stated_centre
    return EditForm(initial=initial, **kwargs)


@sensitive_variables('code')
def render_clip(request, clip, form, status=200, **flags):
    code = form['code'].value()
    return render(request, 'clips/clip.html', {
        'clip': clip,
        'form': form,
        'code': code,
        # Opened with the code, which found this clip (clip_or_refusal), during this request only.
        'threads': code_holder_threads(clip, normalise(code)),
        'reply_max_length': MESSAGE_MAX_LENGTH,
        'failed': clip.status == Clip.Status.FAILED,
        'withdrawn': clip.status == Clip.Status.WITHDRAWN,
        'editable': clip.status in EDITABLE,
        'metadata_size': METADATA_SIZES.get(grid.resolution(clip.metadata_cell)) if clip.metadata_cell else None,
        'can_coarsen': 'metadata_precision' in form.fields,
        **flags,
    }, status=status)


def card_label_suggestion(cleaned):
    """A suggested card label, from the stated date and the first tags. Never stored."""
    parts = []
    if cleaned['stated_time'] is not None:
        parts.append(date_format(timezone.localtime(cleaned['stated_time']), 'SHORT_DATE_FORMAT'))
    names = [str(DEFAULT_TAGS[key]) for key in cleaned['tags']] + cleaned['custom_tags']
    if names:
        parts.append(', '.join(names[:3]))
    return ' – '.join(parts)
