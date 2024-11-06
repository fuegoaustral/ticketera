import logging

from django.db import transaction


def mint_tickets(order):

    try:
        from tickets.models import NewTicket, OrderTicket, Order
        user_already_has_ticket = NewTicket.objects.filter(owner=order.user).exists()
        logging.info(f"user_already_has_ticket {user_already_has_ticket}")
        order_has_more_than_one_ticket_type = order.total_ticket_types() > 1
        logging.info(f"order_has_more_than_one_ticket_type {order_has_more_than_one_ticket_type}")

        order_tickets = OrderTicket.objects.filter(order=order)

        new_minted_tickets = []
        with transaction.atomic():
            for ticket in order_tickets:
                for _ in range(ticket.quantity):
                    new_ticket = NewTicket(
                        holder=order.user,
                        ticket_type=ticket.ticket_type,
                        order=order,
                        event=order.event,
                    )

                    if not user_already_has_ticket and not order_has_more_than_one_ticket_type:
                        new_ticket.owner = order.user
                        user_already_has_ticket = True

                    new_ticket.save()
                    new_minted_tickets.append(new_ticket)

            order.status = Order.OrderStatus.CONFIRMED
            order.save()

            for ticket in new_minted_tickets:
                logging.info(f"Minted {ticket}")


        order.send_confirmation_email()

    except AttributeError as e:
        logging.error(f"Attribute error in minting tickets: {str(e)}")
        raise e

    except Exception as e:
        logging.error(f"Error minting tickets: {str(e)}")
        raise e
