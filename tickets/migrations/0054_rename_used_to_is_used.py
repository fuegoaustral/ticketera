from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0052_merge_20250423_1914'),
    ]

    operations = [
        migrations.RunSQL(
            sql='ALTER TABLE tickets_newticket RENAME COLUMN used TO is_used;',
            reverse_sql='ALTER TABLE tickets_newticket RENAME COLUMN is_used TO used;',
        ),
    ] 