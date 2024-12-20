# Generated by Django 4.2.15 on 2024-08-27 00:40

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0032_alter_newticket_volunteer_ranger_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='newticket',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='newticket',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name='newtickettransfer',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='newtickettransfer',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
    ]
