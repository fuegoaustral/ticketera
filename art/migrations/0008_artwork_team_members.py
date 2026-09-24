from django.db import migrations


def fill_teams(apps, schema_editor):
    """Cada instalación tiene su equipo (Grupo ARTE) con responsable, quienes editan y la logística ya cargada."""
    Artwork = apps.get_model('art', 'Artwork')
    ArtProgram = apps.get_model('art', 'ArtProgram')
    Grupo = apps.get_model('events', 'Grupo')
    GrupoMiembro = apps.get_model('events', 'GrupoMiembro')
    GrupoTipo = apps.get_model('events', 'GrupoTipo')
    User = apps.get_model('auth', 'User')

    art_type, _ = GrupoTipo.objects.get_or_create(
        nombre='ARTE', defaults={'descripcion': 'Instalaciones de arte de Fuego Austral'},
    )
    for artwork in Artwork.objects.exclude(owner=None):
        group = artwork.operations_group
        if group is None:
            program = ArtProgram.objects.filter(event_id=artwork.event_id).first()
            group = Grupo.objects.create(
                event_id=artwork.event_id,
                lider_id=artwork.owner_id,
                nombre=artwork.title or f'Instalación #{artwork.pk}',
                tipo=art_type,
                ingreso_anticipado_amount=program.early_entry_slots if program else 0,
                ingreso_anticipado_desde=program.early_entry_from if program else None,
                late_checkout_amount=program.late_checkout_slots if program else 0,
                late_checkout_hasta=program.late_checkout_until if program else None,
            )
            artwork.operations_group = group
            artwork.save(update_fields=['operations_group'])
        user_ids = {artwork.owner_id, *artwork.collaborators.values_list('pk', flat=True)}
        for email in artwork.logistics_people.values_list('email', flat=True):
            user = User.objects.filter(email__iexact=email).first()
            if user:
                user_ids.add(user.pk)
        for user_id in user_ids:
            GrupoMiembro.objects.get_or_create(grupo=group, user_id=user_id)
    # El responsable del checkout pasa a ser una persona del equipo; se vuelve a elegir.
    Artwork.objects.exclude(checkout_team_responsible=None).update(checkout_team_responsible=None)


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0056_equipo_labels'),
        ('art', '0007_artwork_estafa_contact'),
    ]

    operations = [
        migrations.RunPython(fill_teams, migrations.RunPython.noop),
    ]
