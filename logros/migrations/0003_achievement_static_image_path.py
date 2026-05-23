from django.db import migrations


def use_static_image_path(apps, schema_editor):
    Achievement = apps.get_model('logros', 'Achievement')
    Achievement.objects.filter(slug='3-fiestas-oscuras', image='logros/3-oscuras.jpg').update(
        image='img/logros/3-oscuras.jpg',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('logros', '0002_seed_tres_fiestas_oscuras'),
    ]

    operations = [
        migrations.RunPython(use_static_image_path, migrations.RunPython.noop),
    ]
