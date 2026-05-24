from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('user_profile', '0009_sedesubscriptionplan_billing_cycle'),
    ]

    operations = [
        migrations.AddField(
            model_name='sedesubscription',
            name='is_soft_removed',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='sedesubscription',
            name='soft_removed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
