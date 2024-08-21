from django.http import HttpResponseRedirect

from tickets.models import TicketType, Order
import logging
def is_order_valid(order):
    num_tickets = order.ticket_set.count()
    ticket_types = TicketType.objects.get_available(order.coupon, order.ticket_type.event)

    try:
        ticket_type = ticket_types.get(pk=order.ticket_type.pk)
    except TicketType.DoesNotExist:
        return False

    if ticket_type.available_tickets < num_tickets:
        return False

    if order.coupon and order.coupon.tickets_remaining() < num_tickets:
        return False

    if ticket_type.event.tickets_remaining() < num_tickets:
        return False
    return True

def _complete_order(order):
    logging.info('completing order')
    order.status = Order.OrderStatus.CONFIRMED
    logging.info('saving order')
    order.save()
    logging.info('redirecting')
    return HttpResponseRedirect(order.get_resource_url())

def available_tickets_for_user(user):
    # Implement the logic to calculate the available tickets for the user
    pass
