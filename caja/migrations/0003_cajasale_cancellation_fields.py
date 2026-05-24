from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('caja', '0002_migrate_ticket_stock'),
    ]

    operations = [
        migrations.AddField(
            model_name='cajasale',
            name='notes',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='cajasale',
            name='related_sale',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='cancellations',
                to='caja.cajasale',
            ),
        ),
        migrations.AddField(
            model_name='cajasale',
            name='sale_type',
            field=models.CharField(
                choices=[('SALE', 'Venta'), ('CANCELLATION', 'Cancelación')],
                default='SALE',
                max_length=20,
            ),
        ),
    ]
