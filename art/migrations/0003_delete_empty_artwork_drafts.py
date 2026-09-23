from django.db import migrations


def delete_empty_drafts(apps, schema_editor):
    """Remove the untitled drafts left behind by the old one-click "Nueva obra"."""
    Artwork = apps.get_model('art', 'Artwork')
    Artwork.objects.filter(
        title='', proposal='', status='draft', version=1, operations_group__isnull=True,
        understanding_letter='', collaborators__isnull=True, invitations__isnull=True,
        photos__isnull=True, checkout_photos__isnull=True, grant_items__isnull=True,
        logistics_people__isnull=True, artwork_providers__isnull=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('art', '0002_rename_tables'),
    ]

    operations = [
        migrations.RunPython(delete_empty_drafts, migrations.RunPython.noop),
    ]
