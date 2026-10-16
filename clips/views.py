import datetime

from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_http_methods

from . import probe
from .confirmation import AlreadyConfirmed, confirm_upload
from .forms import ConfirmForm, FileForm
from .models import Original, Precision
from .tags import DEFAULT_TAGS
from .upload_codes import display
from .uploads import size_limit_display

# Fields that error reports must never show (SPEC.md §10).
PRIVATE_FIELDS = ('latitude', 'longitude', 'custom_tags', 'context', 'contact_details')


@require_http_methods(['GET', 'POST'])
def upload(request):
    """Upload step 1: receive the file into quarantine and check that it is a video."""
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


def render_upload(request, form, problem=None, status=200):
    return render(request, 'clips/upload.html', {
        'form': form,
        'problem': problem,
        'size_limit': size_limit_display(),
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


def card_label_suggestion(cleaned):
    """A suggested card label, from the stated date and the first tags. Never stored."""
    parts = []
    if cleaned['stated_time'] is not None:
        parts.append(date_format(timezone.localtime(cleaned['stated_time']), 'SHORT_DATE_FORMAT'))
    names = [str(DEFAULT_TAGS[key]) for key in cleaned['tags']] + cleaned['custom_tags']
    if names:
        parts.append(', '.join(names[:3]))
    return ' – '.join(parts)
