from django.db import migrations


def migrate_ticket_stock(apps, schema_editor):
    TicketType = apps.get_model('tickets', 'TicketType')
    EventProduct = apps.get_model('caja', 'EventProduct')
    EventProductStock = apps.get_model('caja', 'EventProductStock')
    EventProductStockRecord = apps.get_model('caja', 'EventProductStockRecord')

    for ticket_type in TicketType.objects.all():
        product, created = EventProduct.objects.get_or_create(
            ticket_type_id=ticket_type.id,
            defaults={
                'event_id': ticket_type.event_id,
                'name': ticket_type.name,
                'price': ticket_type.price or 0,
                'is_active': True,
            },
        )
        stock, _ = EventProductStock.objects.get_or_create(
            event_product=product,
            defaults={'quantity': ticket_type.ticket_count},
        )
        if created and not EventProductStockRecord.objects.filter(
            event_product=product,
            reason='MIGRATION',
        ).exists():
            EventProductStockRecord.objects.create(
                event_product=product,
                delta=ticket_type.ticket_count,
                reason='MIGRATION',
                balance_after=ticket_type.ticket_count,
                notes='Migración desde ticket_count',
            )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('caja', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(migrate_ticket_stock, noop),
    ]
