import contextlib
import sqlite3

from django.conf import settings
from django.http import FileResponse
from django.shortcuts import render

from maps import data


def licences(request):
    """The archive's licence and source, and the notices of what it serves that others wrote (M7),
    with the date of the place lookup's data, as its licence asks (M9)."""
    return render(request, 'licences.html', {'source_url': settings.SOURCE_URL, 'places_published': places_published()})


def places_published():
    """The publication date of the downloaded Base Adresse Nationale, or None without it."""
    if not data.places_file().exists():
        return None
    # Opened read-only: this page never writes to the index.
    with contextlib.closing(sqlite3.connect(f'{data.places_file().as_uri()}?mode=ro', uri=True)) as index:
        row = index.execute("SELECT value FROM meta WHERE key = 'published'").fetchone()
    return row[0] if row else None


def licence_text(request, name):
    """One of the archive's own licence files (ADR 0010), named by the URL pattern, never by the request."""
    # With a charset: the documentation's licence is not plain ASCII.
    return FileResponse(open(settings.BASE_DIR / name, 'rb'), content_type='text/plain; charset=utf-8')
