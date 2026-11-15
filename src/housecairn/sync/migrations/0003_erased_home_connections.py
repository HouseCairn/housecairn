import uuid

import django.db.models.deletion
from django.db import migrations, models


def lists_to_rows(apps, schema_editor):
    """Move the ids of `ErasedHome.connection_ids` into one row each."""
    erased_home = apps.get_model('sync', 'ErasedHome')
    link = apps.get_model('sync', 'ErasedHomeConnection')
    rows = []
    for erased in erased_home.objects.exclude(connection_ids=[]).iterator():
        for connection_id in set(erased.connection_ids or []):
            rows.append(link(erased=erased, connection_id=uuid.UUID(connection_id)))
    link.objects.bulk_create(rows, batch_size=500, ignore_conflicts=True)


def rows_to_lists(apps, schema_editor):
    erased_home = apps.get_model('sync', 'ErasedHome')
    link = apps.get_model('sync', 'ErasedHomeConnection')
    for erased in erased_home.objects.iterator():
        ids = sorted(
            str(pk)
            for pk in link.objects.filter(erased=erased).values_list('connection_id', flat=True)
        )
        erased_home.objects.filter(pk=erased.pk).update(connection_ids=ids)


class Migration(migrations.Migration):

    dependencies = [
        ('sync', '0002_push'),
    ]

    operations = [
        migrations.CreateModel(
            name='ErasedHomeConnection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('connection_id', models.UUIDField(db_index=True)),
                ('erased', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='connections', to='sync.erasedhome')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('erased', 'connection_id'), name='sync_erased_home_connection')],
            },
        ),
        migrations.RunPython(lists_to_rows, rows_to_lists),
        migrations.RemoveField(
            model_name='erasedhome',
            name='connection_ids',
        ),
    ]
