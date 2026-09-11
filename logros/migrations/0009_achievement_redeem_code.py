# Generated manually for redeem_code support

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('logros', '0008_manage_achievements_optional_condition_and_revoke'),
    ]

    operations = [
        migrations.AddField(
            model_name='achievement',
            name='redeem_code',
            field=models.CharField(
                blank=True,
                help_text=(
                    'Código secreto compartido para canjear este logro en Mis logros. '
                    'Se normaliza a mayúsculas. Vacío = no canjeable por código.'
                ),
                max_length=64,
                null=True,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name='achievement',
            name='condition_type',
            field=models.CharField(
                blank=True,
                choices=[
                    ('purchased_events', 'Compró en eventos'),
                    ('volunteer_at_events', 'Voluntario en eventos'),
                    ('attended_events', 'Asistió a eventos'),
                ],
                help_text='Vacío = solo canje por código o asignación manual (sin auto-unlock).',
                max_length=32,
                null=True,
            ),
        ),
    ]
