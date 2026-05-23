from django.db.models.signals import post_save
from django.dispatch import receiver

from caja.stock import ensure_stock_row, get_or_create_product_for_ticket_type
from tickets.models import TicketType


@receiver(post_save, sender=TicketType)
def ensure_event_product_for_ticket_type(sender, instance, created, **kwargs):
    product = get_or_create_product_for_ticket_type(instance, initial_quantity=instance.ticket_count)
    ensure_stock_row(product)
