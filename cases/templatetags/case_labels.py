"""{% case_label case %}: a case's label, which the lawyer's browser opens (cases.labels).

Until it does, or without JavaScript, the page shows the case's number instead.
"""

from django import template
from django.utils.html import format_html
from django.utils.translation import gettext as _

from cases import labels

register = template.Library()


@register.simple_tag
def case_label(case):
    fallback = _('Case no. %(number)s') % {'number': case.pk}
    if labels.unreadable(case):
        return format_html('<span class="case-label">{}</span>', _('Case no. %(number)s (label unreadable)') % {'number': case.pk})
    return format_html(
        '<span class="case-label" data-label="{}" data-fallback="{}">{}</span>', labels.for_page(case), fallback, fallback,
    )


@register.simple_tag
def case_title(case):
    """The case's number, for a page's title; the browser puts the label in when it opens it."""
    return _('Case no. %(number)s') % {'number': case.pk}
