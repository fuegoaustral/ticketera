from datetime import timedelta

from django.db import migrations, models


def backfill_missing_end(apps, schema_editor):
    EventRequest = apps.get_model('events', 'EventRequest')
    for event_request in EventRequest.objects.filter(end__isnull=True):
        event_request.end = event_request.start + timedelta(hours=6)
        event_request.save(update_fields=['end'])


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0038_alter_eventrequest_max_tickets_default'),
    ]

    operations = [
        migrations.RunPython(backfill_missing_end, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='eventrequest',
            name='end',
            field=models.DateTimeField(),
        ),
    ]
