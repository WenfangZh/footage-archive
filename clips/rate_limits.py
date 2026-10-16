"""Rate limits per connection, for uploads and failed upload-code attempts (SPEC.md §5, §8).

The IP address is used only transiently: as a keyed hash in the cache key, which
expires after RATE_LIMIT_WINDOW_MINUTES. It is never written to the database or logs.

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


def limit(action):
    return {
        UPLOADS: settings.RATE_LIMIT_UPLOADS,
        FAILED_CODES: settings.RATE_LIMIT_FAILED_CODES,
    }[action]


def cache_key(request, action):
    address = request.META.get('REMOTE_ADDR', '')
    digest = salted_hmac(f'clips.rate_limits.{action}', address, algorithm='sha256').hexdigest()
    return f'rate-limit:{action}:{digest}'


def is_limited(request, action):
    return cache.get(cache_key(request, action), 0) >= limit(action)


def count(request, action):
    key = cache_key(request, action)
    cache.add(key, 0, timeout=settings.RATE_LIMIT_WINDOW_MINUTES * 60)
    try:
        cache.incr(key)
    except ValueError:  # Expired between the two calls.
        cache.add(key, 1, timeout=settings.RATE_LIMIT_WINDOW_MINUTES * 60)


def too_many(request):
    response = render(request, 'clips/rate_limited.html', status=429)
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
