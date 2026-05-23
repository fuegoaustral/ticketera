import os
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import migrations, models


def _static_lookup_path(path):
    path = (path or '').lstrip('/')
    if path.startswith('media/'):
        path = path[len('media/') :]
    if path.startswith('img/'):
        return path
    if path.startswith('logros/'):
        return f'img/{path}'
    return path


def migrate_static_images(apps, schema_editor):
    Achievement = apps.get_model('logros', 'Achievement')
    base_dir = Path(settings.BASE_DIR)

    for achievement in Achievement.objects.exclude(image_path=''):
        dest_name = f"logros/{os.path.basename(achievement.image_path.rstrip('/'))}"

        if default_storage.exists(dest_name):
            achievement.image = dest_name
            achievement.save(update_fields=['image', 'updated_at'])
            continue

        static_rel = _static_lookup_path(achievement.image_path)
        found = finders.find(static_rel)
        if not found:
            static_file = base_dir / 'tickets' / 'static' / static_rel
            found = str(static_file) if static_file.is_file() else None

        if not found:
            continue

        with open(found, 'rb') as f:
            saved_name = default_storage.save(dest_name, ContentFile(f.read()))
        achievement.image = saved_name
        achievement.save(update_fields=['image', 'updated_at'])


class Migration(migrations.Migration):

    dependencies = [
        ('logros', '0004_normalize_achievement_image_paths'),
    ]

    operations = [
        migrations.RenameField(
            model_name='achievement',
            old_name='image',
            new_name='image_path',
        ),
        migrations.AddField(
            model_name='achievement',
            name='image',
            field=models.ImageField(blank=True, null=True, upload_to='logros/'),
        ),
        migrations.RunPython(migrate_static_images, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='achievement',
            name='image',
            field=models.ImageField(
                help_text='Imagen del logro (se sube a S3)',
                upload_to='logros/',
            ),
        ),
        migrations.RemoveField(
            model_name='achievement',
            name='image_path',
        ),
    ]
