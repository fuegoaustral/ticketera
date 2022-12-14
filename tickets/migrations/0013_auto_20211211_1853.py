# Generated by Django 3.2.9 on 2021-12-11 18:53

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0012_auto_20211211_1707'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='response',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='order',
            name='status',
            field=models.CharField(choices=[('PENDING', 'Pendiente'), ('CONFIRMED', 'Confirmada'), ('ERROR', 'Error')], default='PENDING', max_length=20),
        ),
    ]
