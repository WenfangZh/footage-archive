from django.conf import settings
from django.http import FileResponse
from django.shortcuts import render


def licences(request):
    """The archive's licence and source, and the notices of what it serves that others wrote (M7)."""
    return render(request, 'licences.html', {'source_url': settings.SOURCE_URL})


def licence_text(request, name):
    """One of the archive's own licence files (ADR 0010), named by the URL pattern, never by the request."""
    # With a charset: the documentation's licence is not plain ASCII.
    return FileResponse(open(settings.BASE_DIR / name, 'rb'), content_type='text/plain; charset=utf-8')
