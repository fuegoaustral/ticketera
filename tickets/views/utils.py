import logging

from django.db.models import Sum
from django.http import HttpResponseRedirect

from events.models import Event
from tickets.models import TicketType, Order


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
    from tickets.models import Order

    try:
        event = Event.objects.get(active=True)
    except Event.DoesNotExist:
        return 0

    # Sum the quantity of tickets in confirmed orders for this event and user
    tickets_bought = (Order.objects
                      .filter(email=user.email)
                      .filter(order_tickets__ticket_type__event=event)
                      .filter(status=Order.OrderStatus.CONFIRMED)
                      .annotate(total_quantity=Sum('order_tickets__quantity'))
                      .aggregate(tickets_bought=Sum('total_quantity'))
                      )['tickets_bought'] or 0

    return event.max_tickets_per_order - tickets_bought
