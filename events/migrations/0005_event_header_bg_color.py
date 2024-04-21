# Generated by Django 3.2.9 on 2023-04-17 21:44

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0004_event_unique_active'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='header_bg_color',
            field=models.CharField(default='#fc0006', help_text='e.g. "#fc0006". The color of the background to fill on bigger screens.', max_length=7),
            preserve_default=False,
        ),
    ]