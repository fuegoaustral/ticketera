from django.db import migrations

OLD = 'Equipo y Servicios de Tareas de Arte de Fuego Austral.'
NEW = 'Equipo de Servicios y Tareas de Arte Fuego Austral.'


def rename(apps, schema_editor):
    # Sólo si nadie la cambió desde el admin.
    apps.get_model('teams', 'Team').objects.filter(slug='estafa', description=OLD).update(description=NEW)


def unrename(apps, schema_editor):
    apps.get_model('teams', 'Team').objects.filter(slug='estafa', description=NEW).update(description=OLD)


class Migration(migrations.Migration):
    dependencies = [('teams', '0002_create_estafa')]

    operations = [migrations.RunPython(rename, unrename)]
