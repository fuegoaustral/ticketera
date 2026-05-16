from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0032_event_ingreso_anticipado_limite_carga'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='show_door_remaining',
            field=models.BooleanField(
                default=False,
                help_text='Si está marcado, en checkout sin entradas online se informa cuántas quedan en puerta.',
            ),
        ),
    ]
