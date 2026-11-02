"""The theme switch (SPEC.md §12 M8.5): light, dark, or the system's, without a script.

The choice is kept in a cookie that holds only the word; the system's choice is no cookie at
all, and any other value counts as it.
"""

import datetime

from django.http import HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

COOKIE = 'theme'
CHOICES = {'light', 'dark'}


def theme(request):
    """For base.html: the chosen theme, or None for the system's."""
    chosen = request.COOKIES.get(COOKIE)
    return {'theme': chosen if chosen in CHOICES else None}


@require_POST
def set_theme(request):
    # Back to the page the form was on, as Django's set_language does: never to another site.
    target = request.POST.get('next', '')
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        target = '/'
    response = HttpResponseRedirect(target)
    chosen = request.POST.get('theme')
    if chosen in CHOICES:
        response.set_cookie(
            COOKIE, chosen, max_age=datetime.timedelta(days=365), samesite='Lax', secure=request.is_secure(),
        )
    else:
        response.delete_cookie(COOKIE, samesite='Lax')
    return response
