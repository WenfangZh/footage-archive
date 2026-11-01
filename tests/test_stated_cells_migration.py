"""Existing clips when the stated location became a set of cells (SPEC.md §12 M6.6, ADR 0009):
each keeps the finest cells of its old cell, in its stated location and its as-uploaded values."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from clips import grid

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = [('clips', '0011_partial_uploads')]
AFTER = [('clips', '0012_stated_cells')]
REPUBLIQUE = (48.8675, 2.3638)


def migrate(targets):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


def test_existing_clips_keep_the_finest_cells_of_their_old_cell():
    street = grid.cell_for_point(*REPUBLIQUE, 10)
    exact = grid.cell_for_point(*REPUBLIQUE, 11)
    try:
        Clip = migrate(BEFORE).get_model('clips', 'Clip')
        moved = Clip.objects.create(
            code_hash='1' * 64, stated_cell=street, stated_precision='street',
            as_uploaded_stated_cell=exact, as_uploaded_stated_precision='exact_spot',
        )
        unplaced = Clip.objects.create(code_hash='2' * 64)

        Clip = migrate(AFTER).get_model('clips', 'Clip')
        moved, unplaced = Clip.objects.get(pk=moved.pk), Clip.objects.get(pk=unplaced.pk)
    finally:
        migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())

    assert moved.stated_cells == sorted(grid.children(street, 11)) and len(moved.stated_cells) == 7
    assert moved.as_uploaded_stated_cells == [exact]
    assert (unplaced.stated_cells, unplaced.as_uploaded_stated_cells) == (None, None)
