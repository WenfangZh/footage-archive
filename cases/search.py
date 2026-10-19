"""Which clips a case finds (SPEC.md §7; GLOSSARY.md: Match, Core match, Margin match).

A clip matches a case when any part of it, by its stated time and duration, falls in the
case's time window and its stated location overlaps the case's area, each widened by the
case's margin. It is a core match when it does so without the margin, and a margin match
otherwise. Only ready clips with both a stated time and a stated location can match; the
metadata time and location are never used. A clip's cell may be coarser than the case's:
it overlaps the area when any of the area's cells lies inside it.
"""

import datetime
from typing import NamedTuple

from django.db.models import DateTimeField, DurationField, ExpressionWrapper, F, Value
from django.db.models.functions import Coalesce

from clips import grid
from clips.models import Clip


class Matches(NamedTuple):
    core: list
    margin: list


class _Overlap:
    """Whether a cell of any resolution overlaps a set of the grid's finest cells."""

    def __init__(self, cells):
        self.cells = cells
        self.coarsened = {}

    def __contains__(self, cell):
        resolution = grid.resolution(cell)
        if resolution not in self.coarsened:
            self.coarsened[resolution] = {grid.coarsen(each, resolution) for each in self.cells}
        return cell in self.coarsened[resolution]


def _candidates(case):
    """Ready clips whose stated time and duration overlap the case's time window with its margin."""
    margin = datetime.timedelta(minutes=case.margin_minutes)
    ends = ExpressionWrapper(
        F('stated_time') + Coalesce('duration', Value(datetime.timedelta(0)), output_field=DurationField()),
        output_field=DateTimeField(),
    )
    return Clip.objects.filter(
        status=Clip.Status.READY, stated_cell__isnull=False, stated_time__lte=case.ends_at + margin,
    ).annotate(stated_end=ends).filter(stated_end__gte=case.starts_at - margin).order_by('stated_time', 'pk')


def find(case):
    """The case's core matches and margin matches, each in order of stated time."""
    area = case.area()
    in_area, near_area = _Overlap(area), _Overlap(grid.widen(area, case.margin_rings))
    matches = Matches([], [])
    for clip in _candidates(case):
        if clip.stated_cell not in near_area:
            continue
        in_window = clip.stated_time <= case.ends_at and clip.stated_end >= case.starts_at
        (matches.core if in_window and clip.stated_cell in in_area else matches.margin).append(clip)
    return matches


def match_of(case, clip):
    """'core', 'margin' or None: how `clip` matches `case`, for pages that show one clip."""
    found = find(case)
    if any(each.pk == clip.pk for each in found.core):
        return 'core'
    if any(each.pk == clip.pk for each in found.margin):
        return 'margin'
    return None
