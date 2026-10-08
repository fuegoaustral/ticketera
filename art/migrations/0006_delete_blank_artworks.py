from django.db import migrations


def delete_blank_artworks(apps, schema_editor):
    """Remove the untitled artworks 0003 missed: saved once, so version > 1, and now 'pending'.

    The title is required on every save, so an untitled artwork can only be a leftover of the
    old one-click "Nueva obra". Anything with attachments or a status decision is kept.
    """
    Artwork = apps.get_model('art', 'Artwork')
    Artwork.objects.filter(
        title='', proposal='', status='pending', status_changed_at__isnull=True,
        operations_group__isnull=True, understanding_letter='', collaborators__isnull=True,
        invitations__isnull=True, photos__isnull=True, checkout_photos__isnull=True,
        grant_items__isnull=True, logistics_people__isnull=True, artwork_providers__isnull=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('art', '0005_rename_obra_to_instalacion'),
    ]

    operations = [
        migrations.RunPython(delete_blank_artworks, migrations.RunPython.noop),
    ]
