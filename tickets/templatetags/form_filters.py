from django import template
from django.conf import settings

from tickets.models import TicketType

register = template.Library()


@register.filter
def absolute_uri(url):
    if not url:
        return ''
    if url.startswith(('http://', 'https://')):
        return url
    base = settings.APP_URL.rstrip('/')
    if not url.startswith('/'):
        url = f'/{url}'
    return f'{base}{url}'


@register.filter
def get_ticket_price(ticket_types, field_name):
    # Extract ticket ID from the field name
    ticket_id = field_name.split('_')[-1]
    try:
        ticket = ticket_types.get(id=ticket_id)
        return ticket.price
    except TicketType.DoesNotExist:
        return 0
