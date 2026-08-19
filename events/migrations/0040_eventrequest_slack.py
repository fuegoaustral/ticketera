from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0039_eventrequest_end_required'),
    ]

    operations = [
        migrations.AddField(
            model_name='eventrequest',
            name='slack_channel',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='eventrequest',
            name='slack_message_ts',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
    ]
