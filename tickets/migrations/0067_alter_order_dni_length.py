from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0066_add_volunteer_mad_to_newticket'),
    ]

    operations = [
        migrations.AlterField(
            model_name='order',
            name='dni',
            field=models.CharField(max_length=50),
        ),
    ]


