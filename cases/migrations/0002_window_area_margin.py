import django.db.models.deletion
import django.db.models.expressions
import django.utils.timezone
from django.db import migrations, models


def refuse_existing_cases(apps, schema_editor):
    # M3's cases had no time window or area, and were never made outside development: one
    # found here would get a made-up window, so stop instead.
    if apps.get_model('cases', 'Case').objects.exists():
        raise RuntimeError('Cases from before M4 have no time window or area. Close them out by hand first.')


class Migration(migrations.Migration):

    dependencies = [
        ('cases', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(refuse_existing_cases, migrations.RunPython.noop),
        # The defaults only fill the columns of rows that the step above has ruled out.
        migrations.AddField(
            model_name='case',
            name='starts_at',
            field=models.DateTimeField(default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='case',
            name='ends_at',
            field=models.DateTimeField(default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='case',
            name='margin_minutes',
            field=models.PositiveSmallIntegerField(default=0),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='case',
            name='margin_rings',
            field=models.PositiveSmallIntegerField(default=0),
            preserve_default=False,
        ),
        migrations.AddConstraint(
            model_name='case',
            constraint=models.CheckConstraint(
                condition=models.Q(('ends_at__gt', django.db.models.expressions.F('starts_at'))),
                name='window_ends_after_it_starts',
            ),
        ),
        migrations.CreateModel(
            name='CaseCell',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cell', models.BigIntegerField()),
                ('case', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cells', to='cases.case')),
            ],
            options={
                'base_manager_name': 'objects',
                'constraints': [models.UniqueConstraint(fields=('case', 'cell'), name='each_cell_once_per_case')],
            },
        ),
    ]
