from urllib.parse import urlencode

from django.db import transaction
from django.urls import reverse

from tickets.models import Order, NewTicket, TicketType, DirectTicketTemplate, DirectTicketTemplateStatus, \
    NewTicketTransfer
from utils.email import send_mail


def direct_sales_existing_user(user, template_tickets, order_type, notes, request_user):
    with transaction.atomic():
        order = Order(
            first_name=user.first_name,
            last_name=user.last_name,
            email=user.email,
            phone=user.profile.phone,
            dni=user.profile.document_number,
            amount=0,
            status=Order.OrderStatus.CONFIRMED,
            event_id=template_tickets[0]['event_id'],
            user=user,
            order_type=order_type,
            donation_art=0,
            donation_venue=0,
            donation_grant=0,
            notes=notes,
            generated_by=request_user
        )
        order.save()

        user_already_has_ticket = NewTicket.objects.filter(owner=user).exists()
        ticket_type = TicketType.objects.get(event_id=template_tickets[0]['event_id'],
                                             is_direct_type=True)
        emitted_tickets = 0
        first_ticket = True
        for template_ticket in template_tickets:
            if template_ticket['amount'] > 0:
                for i in range(template_ticket['amount']):
                    new_ticket = NewTicket(
                        holder=user,
                        ticket_type=ticket_type,
                        event_id=template_ticket['event_id'],
                        order=order
                    )

                    if not user_already_has_ticket and first_ticket:
                        new_ticket.owner = user

                    first_ticket = False
                    new_ticket.save()
                    emitted_tickets += 1

                template = DirectTicketTemplate.objects.get(id=template_ticket['id'])
                template.status = DirectTicketTemplateStatus.ASSIGNED
                template.order = order
                template.amount_used = template_ticket['amount']
                template.save()

        order.amount = emitted_tickets * ticket_type.price
        order.save()

        send_mail(
            template_name='new_transfer_success',
            recipient_list=[user.email],
            context={
                'ticket_count': emitted_tickets,
            }
        )

        return order.id


def direct_sales_new_user(destination_email, template_tickets, order_type, notes, request_user):
    with transaction.atomic():
        order = Order(
            amount=0,
            status=Order.OrderStatus.CONFIRMED,
            event_id=template_tickets[0]['event_id'],
            email=destination_email,

            order_type=order_type,
            donation_art=0,
            donation_venue=0,
            donation_grant=0,
            notes=notes,
            generated_by=request_user
        )
        order.save()

        ticket_type = TicketType.objects.get(event_id=template_tickets[0]['event_id'],
                                             is_direct_type=True)
        emitted_tickets = 0
        for template_ticket in template_tickets:
            print(template_ticket)
            if template_ticket['amount'] > 0:
                for i in range(template_ticket['amount']):
                    new_ticket = NewTicket(
                        ticket_type=ticket_type,
                        event_id=template_ticket['event_id'],
                        order=order,
                    )

                    new_ticket.save()

                    new_ticket_transfer = NewTicketTransfer(
                        ticket=new_ticket,
                        # TODO fix hack, se simula el envio desde el usuario que realiza la compra. Deberiia ser SISTEMA, o implementar tx_from_email
                        tx_from=request_user,

                        tx_to_email=destination_email,
                        status='PENDING'
                    )
                    new_ticket_transfer.save()

                    emitted_tickets += 1

                template = DirectTicketTemplate.objects.get(id=template_ticket['id'])
                template.status = DirectTicketTemplateStatus.PENDING
                template.order = order
                template.amount_used = template_ticket['amount']
                template.save()

        order.amount = emitted_tickets * ticket_type.price
        order.save()

        send_mail(
            template_name='new_transfer_no_account',
            recipient_list=[destination_email],
            context={
                'ticket_count': emitted_tickets,
                'destination_email': destination_email,
                'sign_up_link': f"{reverse('account_signup')}?{urlencode({'email': destination_email})}"
            }
        )
        return order.id
