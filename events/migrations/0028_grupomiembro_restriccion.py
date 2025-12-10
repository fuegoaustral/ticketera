# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0027_alter_grupomiembro_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='grupomiembro',
            name='restriccion',
            field=models.CharField(
                choices=[
                    ('sin_restricciones', 'Sin Restricciones'),
                    ('vegetarian', 'Vegetarian'),
                    ('sin_tacc', 'Sin TACC'),
                    ('particular', 'Particular'),
                ],
                default='sin_restricciones',
                help_text='Restricción alimentaria del miembro',
                max_length=20
            ),
        ),
    ]

