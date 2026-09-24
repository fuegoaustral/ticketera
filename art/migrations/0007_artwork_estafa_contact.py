from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('art', '0006_delete_blank_artworks'),
    ]

    operations = [
        migrations.RenameField(
            model_name='artwork',
            old_name='checkout_art_responsible',
            new_name='estafa_contact',
        ),
        migrations.AlterField(
            model_name='artwork',
            name='estafa_contact',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='estafa_contact_artworks', to=settings.AUTH_USER_MODEL, verbose_name='Contacto de ESTAFA'),
        ),
    ]
