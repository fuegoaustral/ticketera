from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0032_event_ingreso_anticipado_limite_carga'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='soldout_message',
            field=models.TextField(blank=True, default='', help_text='Rich text message shown when tickets are sold out (HTML allowed)'),
        ),
    ]
