# Chatwoot workflow — remove Slack voting fields

from django.db import migrations, models


def migrate_pending_status(apps, schema_editor):
    EventRequest = apps.get_model('events', 'EventRequest')
    EventRequest.objects.filter(status='pending_voting').update(status='pending')


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0034_eventrequest'),
    ]

    operations = [
        migrations.RunPython(migrate_pending_status, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='eventrequest',
            name='voting_ends_at',
        ),
        migrations.RemoveField(
            model_name='eventrequest',
            name='slack_channel',
        ),
        migrations.RemoveField(
            model_name='eventrequest',
            name='slack_message_ts',
        ),
        migrations.AddField(
            model_name='eventrequest',
            name='chatwoot_contact_id',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='eventrequest',
            name='chatwoot_conversation_id',
            field=models.PositiveIntegerField(blank=True, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='eventrequest',
            name='rejection_reason',
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name='eventrequest',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pendiente de revisión'),
                    ('approved', 'Aprobada'),
                    ('rejected', 'Rechazada'),
                    ('cancelled', 'Cancelada'),
                ],
                default='pending',
                max_length=20,
            ),
        ),
        migrations.DeleteModel(
            name='EventRequestVote',
        ),
    ]
