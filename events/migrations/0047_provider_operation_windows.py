from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0046_provider_entry_exit_times'),
    ]

    operations = [
        migrations.RenameField(
            model_name='artworkprovider',
            old_name='entry_date',
            new_name='early_entry_at',
        ),
        migrations.RenameField(
            model_name='artworkprovider',
            old_name='departure_date',
            new_name='dismantling_exit_at',
        ),
        migrations.AlterField(
            model_name='artworkprovider',
            name='early_entry_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Entrada al predio'),
        ),
        migrations.AlterField(
            model_name='artworkprovider',
            name='dismantling_exit_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Salida final del predio'),
        ),
        migrations.AddField(
            model_name='artworkprovider',
            name='early_exit_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Salida del predio'),
        ),
        migrations.AddField(
            model_name='artworkprovider',
            name='dismantling_entry_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Entrada al predio para desarme'),
        ),
    ]
