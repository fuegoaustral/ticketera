from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0036_eventrequest_max_tickets'),
    ]

    operations = [
        migrations.AlterField(
            model_name='eventrequest',
            name='max_tickets',
            field=models.PositiveIntegerField(
                default=500,
                help_text='Cupo máximo total del evento; se replica como stock de cada tipo de entrada.',
            ),
        ),
    ]
