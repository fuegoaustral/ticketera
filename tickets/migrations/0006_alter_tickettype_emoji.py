# Generated by Django 3.2.9 on 2021-12-04 18:45

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0005_tickettype_ticket_count'),
    ]

    operations = [
        migrations.AlterField(
            model_name='tickettype',
            name='emoji',
            field=models.CharField(default='🖕', max_length=20),
        ),
    ]
