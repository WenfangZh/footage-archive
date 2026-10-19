"""Which clips a case finds (SPEC.md §7; GLOSSARY.md: Match, Core match, Margin match).

A clip matches a case when any part of it, by its stated time and duration, falls in the
case's time window and its stated location overlaps the case's area, each widened by the
case's margin. It is a core match when it does so without the margin, and a margin match
otherwise. Only ready clips with both a stated time and a stated location can match; the
metadata time and location are never used. A clip's cell may be coarser than the case's:
it overlaps the area when any of the area's cells lies inside it, as H3 counts it (each
area cell belongs to exactly one coarser cell, even one straddling a boundary). A clip's
cell finer than the case's, possible once the grid is made coarser (ADR 0003), overlaps
the area when the area cell holding it does.
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
        # Every cell of a case's area has the same resolution.
        self.resolution = grid.resolution(next(iter(cells)))

    def __contains__(self, cell):
        if grid.resolution(cell) > self.resolution:
            cell = grid.coarsen(cell, self.resolution)
        resolution = grid.resolution(cell)
        if resolution not in self.coarsened:
            self.coarsened[resolution] = {grid.coarsen(each, resolution) for each in self.cells}
        return cell in self.coarsened[resolution]


def _candidates(case, clips):
    """Ready clips among `clips` whose stated time and duration overlap the case's time window with its margin."""
    margin = datetime.timedelta(minutes=case.margin_minutes)
    ends = ExpressionWrapper(
        F('stated_time') + Coalesce('duration', Value(datetime.timedelta(0)), output_field=DurationField()),
        output_field=DateTimeField(),
    )
    return clips.filter(
        status=Clip.Status.READY, stated_cell__isnull=False, stated_time__lte=case.ends_at + margin,
    ).annotate(stated_end=ends).filter(stated_end__gte=case.starts_at - margin).order_by('stated_time', 'pk').only(
        'id', 'stated_time', 'duration', 'stated_cell', 'stated_precision',
    )


def find(case, clips=None):
    """The case's core matches and margin matches among `clips` (all clips by default), each in order of stated time."""
    area = case.area()
    in_area, near_area = _Overlap(area), _Overlap(grid.widen(area, case.margin_rings))
    matches = Matches([], [])
    for clip in _candidates(case, Clip.objects.all() if clips is None else clips):
        if clip.stated_cell not in near_area:
            continue
        in_window = clip.stated_time <= case.ends_at and clip.stated_end >= case.starts_at
        (matches.core if in_window and clip.stated_cell in in_area else matches.margin).append(clip)
    return matches


def match_of(case, clip):
    """'core', 'margin' or None: how `clip` matches `case`, for pages that show one clip."""
    found = find(case, Clip.objects.filter(pk=clip.pk))
    return 'core' if found.core else 'margin' if found.margin else None
