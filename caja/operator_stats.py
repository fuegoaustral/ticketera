from tickets.models import NewTicket, Order


def ticket_caja_operator_stats(event):
    """Stats shown on caja operator when selling ticket products (same as caja v1)."""
    caja_orders = Order.objects.filter(
        event=event,
        status=Order.OrderStatus.CONFIRMED,
        generated_by_admin_user__isnull=False,
    )
    caja_tickets_sold = NewTicket.objects.filter(
        event=event,
        order__status=Order.OrderStatus.CONFIRMED,
        order__generated_by_admin_user__isnull=False,
    ).count()

    return {
        'caja_tickets_sold': caja_tickets_sold,
        'caja_total_orders': caja_orders.count(),
        'venue_occupancy': event.venue_occupancy,
        'venue_capacity': event.venue_capacity,
        'occupancy_percentage': event.occupancy_percentage,
        'attendees_left': event.attendees_left,
    }
