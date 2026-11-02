from django.conf import settings
from django.shortcuts import render


def licences(request):
    """The archive's licence and source, and the notices of what it serves that others wrote (M7)."""
    return render(request, 'licences.html', {'source_url': settings.SOURCE_URL})
