from django import template

from tickets.models import TicketType

register = template.Library()


@register.filter
def get_ticket_price(ticket_types, field_name):
    # Extract ticket ID from the field name
    ticket_id = field_name.split('_')[-1]
    try:
        ticket = ticket_types.get(id=ticket_id)
        return ticket.price
    except TicketType.DoesNotExist:
        return 0
