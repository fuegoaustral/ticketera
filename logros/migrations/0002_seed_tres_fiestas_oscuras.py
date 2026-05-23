from django.db import migrations


def seed_achievement(apps, schema_editor):
    Achievement = apps.get_model('logros', 'Achievement')
    Achievement.objects.get_or_create(
        slug='3-fiestas-oscuras',
        defaults={
            'name': '3 Fiestas oscuras',
            'image': 'logros/3-oscuras.jpg',
            'description': 'Compraste bonos para tres fiestas oscuras de Fuego Austral.',
            'condition_type': 'purchased_events',
            'condition_config': {'event_ids': [9, 10, 17]},
            'is_active': True,
            'sort_order': 1,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ('logros', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_achievement, migrations.RunPython.noop),
    ]
