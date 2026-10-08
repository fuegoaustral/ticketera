from django.db import migrations, models


def watts_to_text(apps, schema_editor):
    Artwork = apps.get_model('art', 'Artwork')
    for artwork in Artwork.objects.exclude(power_watts=None).only('pk', 'power_watts'):
        Artwork.objects.filter(pk=artwork.pk).update(sound_power=f'{artwork.power_watts} W')


def text_to_watts(apps, schema_editor):
    Artwork = apps.get_model('art', 'Artwork')
    for artwork in Artwork.objects.filter(sound_power__regex=r'^\d+ W$').only('pk', 'sound_power'):
        Artwork.objects.filter(pk=artwork.pk).update(power_watts=int(artwork.sound_power[:-2]))


class Migration(migrations.Migration):

    dependencies = [
        ('art', '0015_art_copy_labels'),
    ]

    operations = [
        migrations.AddField(
            model_name='artwork',
            name='sound_power',
            field=models.CharField(
                blank=True, help_text='Aclará la unidad. Por ejemplo: 1200 W, o 100 dB.', max_length=100,
                verbose_name='Potencia eléctrica máxima en watts y/o decibeles',
            ),
        ),
        migrations.RunPython(watts_to_text, text_to_watts),
        migrations.RemoveField(
            model_name='artwork',
            name='power_watts',
        ),
    ]
