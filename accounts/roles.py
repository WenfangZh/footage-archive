"""Who may see what (SPEC.md §5): the one place that decides a person's role.

A lawyer or the admin also needs a verified second factor in this session; without one,
they are sent to set it up or to enter a code (accounts.two_factor).

Every lawyer page is wrapped in lawyer_required and every admin page in admin_required,
outermost, so that nobody else learns anything from them, not even which methods a
page accepts. The role is read from the database on every request: a suspension or a
revocation applies at once, also to an open session. No response from these pages, a
refusal included, is kept in a browser's cache.
"""

import enum
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.http import Http404
from django.shortcuts import render
from django.utils.cache import add_never_cache_headers
from django.views.defaults import page_not_found

from . import two_factor
from .models import LawyerProfile


class Role(enum.Enum):
    ANONYMOUS = 'anonymous'
    ADMIN = 'admin'
    # Admitted and not suspended (GLOSSARY.md).
    LAWYER = 'lawyer'
    APPLICANT = 'applicant'
    # Suspended, revoked or declined, or an account that is neither the admin's nor a lawyer's.
    NO_ACCESS = 'no_access'


def role(user):
    # Django treats an inactive account's session as logged out: such a user is anonymous.
    if not user.is_authenticated:
        return Role.ANONYMOUS
    if not user.is_active:
        return Role.NO_ACCESS
    if user.is_admin:
        return Role.ADMIN
    status = LawyerProfile.objects.filter(user=user).values_list('status', flat=True).first()
    if status == LawyerProfile.Status.LAWYER:
        return Role.LAWYER
    if status == LawyerProfile.Status.APPLICANT:
        return Role.APPLICANT
    return Role.NO_ACCESS


def no_access(request):
    return render(request, 'accounts/no_access.html', status=403)


def _required(needed):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            current = role(request.user)
            if current == Role.ANONYMOUS:
                response = redirect_to_login(request.get_full_path())
            elif current != needed:
                response = no_access(request)
            elif not request.user.is_verified():
                response = two_factor.required_step(request)
            else:
                try:
                    response = view(request, *args, **kwargs)
                except Http404 as exception:
                    # Answered here, so that a page not found is not cached either.
                    response = page_not_found(request, exception)
            add_never_cache_headers(response)
            return response
        return wrapped
    return decorator


lawyer_required = _required(Role.LAWYER)
admin_required = _required(Role.ADMIN)
