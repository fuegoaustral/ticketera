from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0035_eventrequest_chatwoot'),
    ]

    operations = [
        migrations.AddField(
            model_name='eventrequest',
            name='max_tickets',
            field=models.PositiveIntegerField(
                default=100,
                help_text='Cupo máximo total del evento; se replica como stock de cada tipo de entrada.',
            ),
            preserve_default=False,
        ),
    ]
