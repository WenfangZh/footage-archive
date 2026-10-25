"""{% unlock_form %}: the form that unlocks the personal key on a page that needs it
(keys/templates/keys/unlock.html), for an applicant or lawyer who has one; nothing otherwise.
With it, where their browser says how it takes part in a rotation of the group key
(keys/templates/keys/rotation.html): it is used only on lawyers' pages."""

from django import template

from keys.grants import current_key
from keys.views import unlock_context

register = template.Library()


@register.inclusion_tag('keys/unlock_tag.html', takes_context=True)
def unlock_form(context):
    request = context['request']
    profile = getattr(request.user, 'lawyer_profile', None)
    key = profile and current_key(profile)
    return {'key': key, 'csrf_token': context.get('csrf_token'), **(unlock_context(request, key) if key else {})}
