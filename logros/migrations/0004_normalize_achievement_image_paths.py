from django.db import migrations


def normalize_image_paths(apps, schema_editor):
    Achievement = apps.get_model('logros', 'Achievement')
    for achievement in Achievement.objects.all():
        path = (achievement.image or '').lstrip('/')
        if path.startswith('media/'):
            path = path[len('media/') :]
        if path.startswith('img/'):
            continue
        if path.startswith('logros/'):
            achievement.image = f'img/{path}'
            achievement.save(update_fields=['image', 'updated_at'])


class Migration(migrations.Migration):

    dependencies = [
        ('logros', '0003_achievement_static_image_path'),
    ]

    operations = [
        migrations.RunPython(normalize_image_paths, migrations.RunPython.noop),
    ]
