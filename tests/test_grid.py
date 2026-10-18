import h3.api.basic_int as h3
import pytest

from clips import grid

# A public place (Place de la Bastille, Paris), not anyone's position.
LATITUDE, LONGITUDE = 48.8532, 2.3692


def test_a_point_snaps_to_a_cell_of_the_requested_resolution():
    for resolution in (11, 10, 9):
        cell = grid.cell_for_point(LATITUDE, LONGITUDE, resolution)
        assert grid.is_cell(cell)
        assert grid.resolution(cell) == resolution


def test_points_in_the_same_cell_give_the_same_value():
    cell = grid.cell_for_point(LATITUDE, LONGITUDE, 11)
    centre_latitude, centre_longitude = grid.centre(cell)
    assert grid.cell_for_point(centre_latitude, centre_longitude, 11) == cell
    assert grid.centre(cell) != (LATITUDE, LONGITUDE)


@pytest.mark.parametrize('requested', [12, 15])
def test_never_finer_than_resolution_11(requested):
    cell = grid.cell_for_point(LATITUDE, LONGITUDE, requested)
    assert grid.resolution(cell) == 11


def test_the_grid_setting_caps_every_resolution(settings):
    settings.GRID_H3_RESOLUTION = 10
    assert grid.resolution(grid.cell_for_point(LATITUDE, LONGITUDE, 11)) == 10
    assert grid.resolution(grid.cell_for_point(LATITUDE, LONGITUDE, 9)) == 9


def test_finest_cells_are_about_50_m_across_in_paris():
    # ADR 0003: 1,840–2,226 m² over metropolitan France; a 50 m square is 2,500 m².
    cell = grid.cell_for_point(LATITUDE, LONGITUDE, 11)
    assert 1_800 < h3.cell_area(cell, 'm^2') < 2_300


def test_coarsen_gives_the_cell_containing_it():
    cell = grid.cell_for_point(LATITUDE, LONGITUDE, 11)
    coarser = grid.coarsen(cell, 9)
    assert grid.resolution(coarser) == 9
    assert coarser == grid.cell_for_point(LATITUDE, LONGITUDE, 9)
    assert grid.coarsen(cell, 11) == cell


def test_coarsen_refuses_to_make_a_cell_finer():
    cell = grid.cell_for_point(LATITUDE, LONGITUDE, 9)
    with pytest.raises(ValueError, match='finer'):
        grid.coarsen(cell, 10)


@pytest.mark.parametrize('latitude, longitude', [(91, 0), (-90.5, 0), (0, 180.5), (0, -181)])
def test_positions_off_the_earth_are_refused(latitude, longitude):
    with pytest.raises(ValueError, match='Not a position on Earth'):
        grid.cell_for_point(latitude, longitude, 11)


@pytest.mark.parametrize('value', [12345, -1, '8b1fb4644934fff', None])
def test_is_cell_rejects_other_values(value):
    assert not grid.is_cell(value)


def test_widening_adds_rings_around_every_cell():
    cell = grid.cell_for_point(LATITUDE, LONGITUDE, 11)
    assert grid.widen([cell], 0) == {cell}
    assert len(grid.widen([cell], 1)) == 7 and len(grid.widen([cell], 2)) == 19
    far = grid.cell_for_point(LATITUDE + 1, LONGITUDE, 11)
    assert grid.widen([cell, far], 1) == grid.widen([cell], 1) | grid.widen([far], 1)


def corner_to_corner(cells):
    """The true width of a set of cells: its two farthest corners, found the slow way."""
    corners = [corner for cell in cells for corner in h3.cell_to_boundary(cell)]
    return max(h3.great_circle_distance(a, b, 'm') for a in corners for b in corners)


@pytest.mark.parametrize('shape', ['blob', 'ring', 'two clusters', 'line'])
def test_a_span_is_never_less_than_the_true_width_and_only_a_little_more(shape):
    cell = grid.cell_for_point(LATITUDE, LONGITUDE, 11)
    far = grid.cell_for_point(LATITUDE + 0.005, LONGITUDE + 0.004, 11)
    cells = {
        'blob': grid.widen([cell], 4) | grid.widen([h3.grid_disk(cell, 5)[-1]], 2),
        'ring': set(h3.grid_ring(cell, 6)),
        'two clusters': grid.widen([cell, far], 2),
        'line': set(h3.grid_path_cells(cell, far)),
    }[shape]
    true_width = corner_to_corner(cells)
    assert true_width <= grid.span_metres(cells) <= true_width + 10
    widened = grid.widen(cells, 2)
    assert corner_to_corner(widened) <= grid.span_metres(cells, rings=2) <= corner_to_corner(widened) + 20


def test_a_span_is_measured_from_edge_to_edge():
    cell = grid.cell_for_point(LATITUDE, LONGITUDE, 11)
    assert 50 < grid.span_metres([cell]) < 65
    # Each ring adds about one cell spacing (about 50 m) on each side.
    assert 1_900 < grid.span_metres(grid.widen([cell], 19)) < 2_000
    two_apart = grid.cell_for_point(LATITUDE + 0.01, LONGITUDE, 11)
    assert 1_100 < grid.span_metres([cell, two_apart]) < 1_200
