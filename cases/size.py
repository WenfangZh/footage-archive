"""A case's size, margin included, against the size caps and ceilings (SPEC.md §7)."""

from django.conf import settings

from clips import grid


class CaseTooLarge(ValueError):
    """Over a size cap, a case needs the admin's approval; beyond a ceiling, nothing allows it."""

    def __init__(self, window_minutes, area_metres, beyond_ceiling):
        self.window_minutes, self.area_metres, self.beyond_ceiling = window_minutes, area_metres, beyond_ceiling
        limit = 'ceiling' if beyond_ceiling else 'size caps'
        super().__init__(f'Over the {limit}: {window_minutes:.0f} minutes and {area_metres:.0f} m across.')


def window_minutes(starts_at, ends_at, margin_minutes):
    """The time window's length with the margin added at both ends."""
    return (ends_at - starts_at).total_seconds() / 60 + 2 * margin_minutes


def area_metres(cells, margin_rings):
    """How far across the area is with its margin of rings."""
    return grid.span_metres(grid.widen(cells, margin_rings))


def check(starts_at, ends_at, cells, margin_minutes, margin_rings):
    """Raise CaseTooLarge for a case over the caps, saying whether it is beyond a ceiling too."""
    window = window_minutes(starts_at, ends_at, margin_minutes)
    area = area_metres(cells, margin_rings)
    if window > settings.CASE_WINDOW_CEILING_MINUTES or area > settings.CASE_AREA_CEILING_METRES:
        raise CaseTooLarge(window, area, beyond_ceiling=True)
    if window > settings.CASE_WINDOW_CAP_MINUTES or area > settings.CASE_AREA_CAP_METRES:
        raise CaseTooLarge(window, area, beyond_ceiling=False)
