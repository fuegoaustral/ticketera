from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('caja', '0003_cajasale_cancellation_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='eventproduct',
            name='image',
            field=models.ImageField(blank=True, null=True, upload_to='caja/products/'),
        ),
    ]
