"""The grid that every stored location is snapped to (ADR 0002, 0003, 0004).

Cells are H3 cells, stored as their 64-bit integer id. A precise point is only ever
used to find its cell, then dropped: two points in the same cell give the same value.
No cell is finer than settings.GRID_H3_RESOLUTION (11, about 50 m across, at most).
"""

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
