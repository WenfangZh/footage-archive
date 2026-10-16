import datetime

from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from . import probe
from .forms import FileForm
from .models import Original
from .uploads import size_limit_display


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
@require_http_methods(['GET'])
def confirm(request, original_id):
    """Upload step 2: the form, pre-filled from the quick read. The fields come in step 6."""
    original = unconfirmed_original_or_404(original_id)
    read = probe.quick_read(original.file.path)
    return render(request, 'clips/confirm.html', {'read': read})


def unconfirmed_original_or_404(original_id):
    deadline = timezone.now() - datetime.timedelta(minutes=settings.UNCONFIRMED_ORIGINAL_MINUTES)
    original = Original.objects.filter(
        id=original_id, clip__isnull=True, received_at__gt=deadline,
    ).first()
    if original is None:
        raise Http404
    return original
