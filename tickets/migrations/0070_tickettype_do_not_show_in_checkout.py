from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0069_add_left_at_to_newticket'),
    ]

    operations = [
        migrations.AddField(
            model_name='tickettype',
            name='do_not_show_in_checkout',
            field=models.BooleanField(default=False),
        ),
    ]
