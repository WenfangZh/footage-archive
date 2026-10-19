"""The grid that every stored location is snapped to (ADR 0002, 0003, 0004).

Cells are H3 cells, stored as their 64-bit integer id. A precise point is only ever
used to find its cell, then dropped: two points in the same cell give the same value.
No cell is finer than settings.GRID_H3_RESOLUTION (11, about 50 m across, at most).
"""

from itertools import combinations
from math import cos, radians

import h3.api.basic_int as h3
from django.conf import settings


def finest_resolution():
    return settings.GRID_H3_RESOLUTION


def cell_for_point(latitude, longitude, resolution):
    """The cell containing a point, at `resolution` or the grid's limit if that is coarser."""
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        # H3 itself accepts out-of-range values without complaint.
        raise ValueError(f'Not a position on Earth: {latitude}, {longitude}')
    return h3.latlng_to_cell(latitude, longitude, min(resolution, finest_resolution()))


def coarsen(cell, resolution):
    """The coarser cell containing `cell`. Never finer: the finer position is gone."""
    if resolution > h3.get_resolution(cell):
        raise ValueError(f'Resolution {resolution} is finer than the cell ({h3.get_resolution(cell)}).')
    return h3.cell_to_parent(cell, resolution)


def resolution(cell):
    return h3.get_resolution(cell)


def centre(cell):
    """The centre of a cell as (latitude, longitude), e.g. to pre-fill a form."""
    return h3.cell_to_latlng(cell)


def is_cell(value):
    # H3 raises on integers outside 64 unsigned bits instead of answering False.
    return isinstance(value, int) and 0 <= value < 2**64 and h3.is_valid_cell(value)


def widen(cells, rings):
    """The cells within `rings` steps of any of `cells`, those included."""
    widened = set()
    for cell in cells:
        widened.update(h3.grid_disk(cell, rings))
    return widened


def span_metres(cells, rings=0):
    """How far across a set of cells of one resolution is, edge to edge, once widened by `rings`.

    Never less than the truth: at resolution 11, a few metres more, plus up to about 13 m
    per ring. The farthest two centres are corners of the centres' convex hull, so only
    those are compared. Beyond each of the two lies at most half a cell (a centre-to-corner
    distance), and each ring adds at most one centre-to-centre spacing at each end (rings
    reach less far in some directions), so the widened set is never built. Both are taken
    at their largest among the hull's cells: over the few kilometres a case spans, cells
    differ in size by a tiny fraction.
    """
    centres = {cell: h3.cell_to_latlng(cell) for cell in set(cells)}
    if not centres:
        raise ValueError('An empty set of cells has no span.')
    if len({h3.get_resolution(cell) for cell in centres}) > 1:
        raise ValueError('A span is measured over cells of one resolution.')
    hull = _convex_hull(centres)
    farthest = max((_metres(centres[a], centres[b]) for a, b in combinations(hull, 2)), default=0)
    half_width = max(_metres(centres[cell], corner) for cell in hull for corner in h3.cell_to_boundary(cell))
    spacing = max(spacing_metres(cell) for cell in hull)
    return farthest + 2 * half_width + 2 * rings * spacing


def spacing_metres(cell):
    """The largest distance from a cell's centre to a neighbour's: how far one ring reaches."""
    centre = h3.cell_to_latlng(cell)
    return max(_metres(centre, h3.cell_to_latlng(near)) for near in h3.grid_ring(cell, 1))


def cells_around(latitude, longitude, radius_metres):
    """The cells containing the circle of `radius_metres` around a point, and no others that it
    could not reach.

    The point is used only to find its grid cell, then dropped: the cells depend on that
    cell alone, so they tell no more about the point than it does. Wherever the point lies
    in its cell, it is at most a centre-to-corner distance from the centre, so the cells
    kept are those that come within the radius plus that distance of the centre.
    """
    centre = cell_for_point(latitude, longitude, finest_resolution())
    if radius_metres <= 0:
        return {centre}
    middle = h3.cell_to_latlng(centre)
    # Distances here are on a sphere, which is off by up to about 0.3% on the real Earth.
    reach = (radius_metres + _corner_metres(centre)) * 1.005

    def within_reach(cell):
        return _metres(middle, h3.cell_to_latlng(cell)) - _corner_metres(cell) <= reach

    cells, rings = {centre}, 0
    # Grow ring by ring until a whole ring is out of reach: every farther ring is too.
    while True:
        rings += 1
        reached = {cell for cell in h3.grid_ring(centre, rings) if within_reach(cell)}
        if not reached:
            return cells
        cells |= reached


def _corner_metres(cell):
    """How far a cell reaches from its centre: to its farthest corner."""
    middle = h3.cell_to_latlng(cell)
    return max(_metres(middle, corner) for corner in h3.cell_to_boundary(cell))


def _metres(a, b):
    return h3.great_circle_distance(a, b, 'm')


def _convex_hull(centres):
    """The cells whose centres are corners of the convex hull of all `centres`.

    Andrew's monotone chain, on a flat projection around one of the centres: accurate
    enough over a few kilometres, and a set spread wider is far beyond any case's ceiling.
    Across the 180° meridian or near a pole it measures too wide, which only refuses.
    """
    latitude = next(iter(centres.values()))[0]
    stretch = cos(radians(latitude))
    points = sorted((lng * stretch, lat, cell) for cell, (lat, lng) in centres.items())
    if len(points) <= 2:
        return [cell for _, _, cell in points]

    def turns_left(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]) > 0

    def chain(ordered):
        kept = []
        for point in ordered:
            while len(kept) >= 2 and not turns_left(kept[-2], kept[-1], point):
                kept.pop()
            kept.append(point)
        return kept[:-1]

    return [cell for _, _, cell in chain(points) + chain(reversed(points))]
