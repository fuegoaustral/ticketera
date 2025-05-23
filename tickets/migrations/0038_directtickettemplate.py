# Generated by Django 4.2.15 on 2024-10-20 22:19

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0009_event_max_tickets_per_order'),
        ('tickets', '0036_order_notes'),
    ]

    operations = [
        migrations.CreateModel(
            name='DirectTicketTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('origin', models.CharField(choices=[('CAMP', 'Camp'), ('VOLUNTARIOS', 'Voluntarios'), ('ARTE', 'Arte')], default='CAMP', max_length=20)),
                ('name', models.CharField(help_text='Descripción o referencia', max_length=255)),
                ('amount', models.PositiveIntegerField()),
                ('event', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='events.event')),
            ],
        ),
    ]
