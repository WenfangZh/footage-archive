# A clip's stated location becomes the set of the grid's finest cells (ADR 0009). Each clip keeps
# the finest cells of its old cell, in its stated location and its as-uploaded values alike; the
# precision goes, since the cells now carry it.

import h3.api.basic_int as h3
from django.conf import settings
from django.db import migrations, models


def finest_cells(cell):
    if cell is None:
        return None
    finest = settings.GRID_H3_RESOLUTION
    if h3.get_resolution(cell) > finest:  # Only if the grid was made coarser since.
        return [h3.cell_to_parent(cell, finest)]
    return sorted(h3.cell_to_children(cell, finest))


def to_cells(apps, schema_editor):
    Clip = apps.get_model('clips', 'Clip')
    for clip in Clip.objects.exclude(stated_cell=None, as_uploaded_stated_cell=None).only(
        'stated_cell', 'as_uploaded_stated_cell',
    ):
        Clip.objects.filter(pk=clip.pk).update(
            stated_cells=finest_cells(clip.stated_cell),
            as_uploaded_stated_cells=finest_cells(clip.as_uploaded_stated_cell),
        )


def refuse_if_placed(apps, schema_editor):
    """The cells cannot tell which precision they came from: undoing this would leave every clip
    without a place. Refused, unless no clip has one."""
    Clip = apps.get_model('clips', 'Clip')
    if Clip.objects.exclude(stated_cells=None, as_uploaded_stated_cells=None).exists():
        raise RuntimeError('Clips have places as cells, which cannot be turned back into a cell and a precision.')


class Migration(migrations.Migration):

    dependencies = [
        ('clips', '0011_partial_uploads'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='clip',
            name='stated_location_has_cell_and_precision',
        ),
        migrations.RemoveConstraint(
            model_name='clip',
            name='as_uploaded_location_has_cell_and_precision',
        ),
        migrations.AddField(
            model_name='clip',
            name='as_uploaded_stated_cells',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='clip',
            name='stated_cells',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.RunPython(to_cells, refuse_if_placed),
        migrations.RemoveField(
            model_name='clip',
            name='as_uploaded_stated_cell',
        ),
        migrations.RemoveField(
            model_name='clip',
            name='as_uploaded_stated_precision',
        ),
        migrations.RemoveField(
            model_name='clip',
            name='stated_cell',
        ),
        migrations.RemoveField(
            model_name='clip',
            name='stated_precision',
        ),
        migrations.AddConstraint(
            model_name='clip',
            constraint=models.CheckConstraint(condition=models.Q(('stated_cells', []), _negated=True), name='stated_location_not_empty'),
        ),
        migrations.AddConstraint(
            model_name='clip',
            constraint=models.CheckConstraint(condition=models.Q(('as_uploaded_stated_cells', []), _negated=True), name='as_uploaded_location_not_empty'),
        ),
    ]
