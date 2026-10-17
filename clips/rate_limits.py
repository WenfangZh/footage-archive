"""Rate limits for uploads, failed upload-code attempts and failed logins (SPEC.md §5, §8, §10).

Counts are kept per connection, and failed logins also per account on that connection, so
that nobody can lock a lawyer out from elsewhere. The IP address and
the email address typed are used only transiently: as a keyed hash in the cache key,
which expires after RATE_LIMIT_WINDOW_MINUTES. They are never written to the database
or logs.

Each window starts at a connection's first counted event and lasts the whole window.
Many phones at one protest can share an IP address (mobile carriers share them), so
the limits are generous: they stop floods, not people.
"""

from django.conf import settings
from django.core.cache import cache
from django.shortcuts import render
from django.urls import reverse
from django.utils.crypto import salted_hmac

UPLOADS = 'uploads'
FAILED_CODES = 'failed_codes'
FAILED_LOGINS = 'failed_logins'
FAILED_LOGINS_FOR_ACCOUNT = 'failed_logins_for_account'


def limit(action):
    return {
        UPLOADS: settings.RATE_LIMIT_UPLOADS,
        FAILED_CODES: settings.RATE_LIMIT_FAILED_CODES,
        FAILED_LOGINS: settings.RATE_LIMIT_FAILED_LOGINS,
        FAILED_LOGINS_FOR_ACCOUNT: settings.RATE_LIMIT_FAILED_LOGINS_FOR_ACCOUNT,
    }[action]


def cache_key(request, action, account=None):
    # Per connection, or per account on that connection when one is given (the email address
    # as typed, normalised).
    address = request.META.get('REMOTE_ADDR', '')
    subject = address if account is None else f'{account}\n{address}'
    digest = salted_hmac(f'clips.rate_limits.{action}', subject, algorithm='sha256').hexdigest()
    return f'rate-limit:{action}:{digest}'


def is_limited(request, action, account=None):
    return cache.get(cache_key(request, action, account), 0) >= limit(action)


def count(request, action, account=None):
    """Count one event; returns the count so far in this window."""
    key = cache_key(request, action, account)
    cache.add(key, 0, timeout=settings.RATE_LIMIT_WINDOW_MINUTES * 60)
    try:
        return cache.incr(key)
    except ValueError:  # Expired between the two calls.
        cache.add(key, 1, timeout=settings.RATE_LIMIT_WINDOW_MINUTES * 60)
        return 1


def uncount(request, action, account=None):
    """Take back one event counted in advance."""
    try:
        cache.decr(cache_key(request, action, account))
    except ValueError:  # The window ended meanwhile.
        pass


def too_many(request, template_name='clips/rate_limited.html'):
    response = render(request, template_name, status=429)
    response['Retry-After'] = str(settings.RATE_LIMIT_WINDOW_MINUTES * 60)
    return response


class UploadRateLimitMiddleware:
    """Counts upload attempts and refuses those over the limit before the file is read.

    It must come before CsrfViewMiddleware, which reads the whole request body.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == 'POST' and request.path == reverse('clips:upload'):
            if is_limited(request, UPLOADS):
                return too_many(request)
            count(request, UPLOADS)
        return self.get_response(request)
