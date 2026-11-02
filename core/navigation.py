"""The header's links to each role's pages (SPEC.md §12 M8).

Only links: every page still checks the role itself (accounts.roles). They are shown once the
second factor is verified, since until then each of these pages sends the person to it.
"""

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from accounts.roles import Role, role

PAGES = {
    Role.LAWYER: [
        ('lawyer:home', _('Your cases')),
        ('lawyer:messages', _('Your messages')),
        ('accounts:key', _('Your personal key')),
        ('lawyer:settings', _('Settings')),
    ],
    Role.ADMIN: [
        ('manage:home', _('Administration')),
        ('manage:invite', _('Send an invitation')),
        ('manage:case_requests', _('Requests for larger cases')),
        ('manage:disputed_tags', _('Clips with disputed tags')),
        ('manage:withdrawn_clips', _('Withdrawn clips')),
    ],
}


def navigation(request):
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated or not user.is_verified():
        return {'navigation': []}
    match = getattr(request, 'resolver_match', None)
    current = match.view_name if match else None
    return {'navigation': [
        {'url': reverse(name), 'label': label, 'current': name == current}
        for name, label in PAGES.get(role(user), [])
    ]}
