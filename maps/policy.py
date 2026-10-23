"""The Content Security Policy of a page showing the map (SPEC.md §12 M4.5, ADR 0006).

The browser itself refuses anything from another origin: scripts, styles, fonts, images,
workers and requests for data all come from the archive, and the page cannot be framed.
"""

from functools import wraps

POLICY = '; '.join([
    "default-src 'self'",
    "img-src 'self' data: blob:",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


def map_page(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response['Content-Security-Policy'] = POLICY
        return response
    return wrapped
