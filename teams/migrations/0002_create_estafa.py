from django.db import migrations


def create_estafa(apps, schema_editor):
    Team = apps.get_model('teams', 'Team')
    Team.objects.get_or_create(slug='estafa', defaults={
        'name': 'ESTAFA',
        'description': 'Equipo y Servicios de Tareas de Arte de Fuego Austral.',
    })


class Migration(migrations.Migration):
    dependencies = [('teams', '0001_initial')]

    operations = [migrations.RunPython(create_estafa, migrations.RunPython.noop)]
