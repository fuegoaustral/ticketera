from django.db import migrations

ART_MODELS = (
    'artprogram',
    'artwork',
    'artworkcheckoutphoto',
    'artworkgrantitem',
    'artworkgrantitemphoto',
    'artworkinvitation',
    'artworklogisticsperson',
    'artworkphoto',
    'artworkprovider',
    'artworkprovidervehicle',
)


def move_content_types(apps, schema_editor, source, target):
    """Keep audit log entries and permissions attached to the moved models."""
    ContentType = apps.get_model('contenttypes', 'ContentType')
    for model in ART_MODELS:
        if not ContentType.objects.filter(app_label=target, model=model).exists():
            ContentType.objects.filter(app_label=source, model=model).update(app_label=target)


def forwards(apps, schema_editor):
    move_content_types(apps, schema_editor, 'events', 'art')


def backwards(apps, schema_editor):
    move_content_types(apps, schema_editor, 'art', 'events')


class Migration(migrations.Migration):

    dependencies = [
        ('art', '0001_initial'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        *(migrations.AlterModelTable(name=model, table=None) for model in ART_MODELS),
        migrations.RunPython(forwards, backwards),
    ]
