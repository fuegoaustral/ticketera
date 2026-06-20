# Generated manually for EventRequest workflow

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('events', '0033_event_show_door_remaining'),
    ]

    operations = [
        migrations.CreateModel(
            name='EventRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=255)),
                ('description', models.TextField()),
                ('start', models.DateTimeField()),
                ('end', models.DateTimeField(blank=True, null=True)),
                ('header_image', models.ImageField(upload_to='events/event_requests')),
                ('location', models.CharField(max_length=255)),
                ('location_url', models.URLField(blank=True, max_length=500)),
                ('status', models.CharField(
                    choices=[
                        ('pending_voting', 'En votación'),
                        ('approved', 'Aprobada'),
                        ('rejected', 'Rechazada'),
                        ('cancelled', 'Cancelada'),
                    ],
                    default='pending_voting',
                    max_length=20,
                )),
                ('voting_ends_at', models.DateTimeField()),
                ('slack_channel', models.CharField(blank=True, max_length=64)),
                ('slack_message_ts', models.CharField(blank=True, max_length=32)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('created_event', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='source_request',
                    to='events.event',
                )),
                ('requested_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='event_requests',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='EventRequestTicketType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=100)),
                ('description', models.TextField(blank=True, max_length=2000)),
                ('price', models.DecimalField(decimal_places=2, max_digits=10)),
                ('event_request', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='ticket_types',
                    to='events.eventrequest',
                )),
            ],
            options={
                'ordering': ['id'],
            },
        ),
        migrations.CreateModel(
            name='EventRequestVote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('slack_user_id', models.CharField(max_length=32)),
                ('slack_user_name', models.CharField(blank=True, max_length=255)),
                ('vote', models.CharField(
                    choices=[('up', 'A favor'), ('down', 'En contra')],
                    max_length=8,
                )),
                ('event_request', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='votes',
                    to='events.eventrequest',
                )),
            ],
            options={
                'unique_together': {('event_request', 'slack_user_id')},
            },
        ),
    ]
