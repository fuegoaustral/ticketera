# Generated by Django 3.2.9 on 2021-12-04 17:43

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0003_auto_20211204_1742'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='tickettype',
            name='tickets_tickettype_price_or_price_with_coupon',
        ),
    ]
