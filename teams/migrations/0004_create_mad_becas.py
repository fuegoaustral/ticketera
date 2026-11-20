from django.db import migrations

TEAMS = (
    ('mad', 'MAD', 'Ve todas las propuestas de Arte, sin editarlas.'),
    ('becas', 'Becas', 'Ve todas las propuestas de Arte y gestiona sus becas.'),
)


def create_teams(apps, schema_editor):
    Team = apps.get_model('teams', 'Team')
    for slug, name, description in TEAMS:
        if not Team.objects.filter(slug=slug).exists() and not Team.objects.filter(name=name).exists():
            Team.objects.create(slug=slug, name=name, description=description)


class Migration(migrations.Migration):
    dependencies = [('teams', '0003_rename_estafa_description')]

    operations = [migrations.RunPython(create_teams, migrations.RunPython.noop)]
