from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.urls import reverse

from events.models import Event
from tickets.models import NewTicket, NewTicketTransfer


@login_required
def my_fire_view(request):
    return redirect(reverse('my_tickets'))


@login_required
def my_tickets_view(request):
    event = Event.objects.get(active=True)
    my_ticket = NewTicket.objects.filter(holder=request.user, event=event, owner=request.user).first()
    tickets = NewTicket.objects.filter(holder=request.user, event=event, owner=None).all()

    tickets_dto = []

    for ticket in tickets:
        tickets_dto.append(ticket.get_dto(user=request.user))

    has_unassigned_tickets = any(ticket['is_owners'] is False for ticket in tickets_dto)
    has_transfer_pending = any(ticket['is_transfer_pending'] is True for ticket in tickets_dto)

    transferred_tickets = NewTicketTransfer.objects.filter(tx_from=request.user, status='COMPLETED').all()
    transferred_dto = []
    for transfer in transferred_tickets:
        transferred_dto.append({
            'tx_to_email': transfer.tx_to_email,
            'ticket_key': transfer.ticket.key,
            'ticket_type': transfer.ticket.ticket_type.name,
            'ticket_color': transfer.ticket.ticket_type.color,
            'emoji': transfer.ticket.ticket_type.emoji,
        })

    return render(request, 'mi_fuego/my_tickets/index.html', {
        'is_volunteer': my_ticket.is_volunteer() if my_ticket else False,
        'my_ticket': my_ticket.get_dto(user=request.user) if my_ticket else None,
        'has_unassigned_tickets': has_unassigned_tickets,
        'has_transfer_pending': has_transfer_pending,
        'tickets_dto': tickets_dto,
        'transferred_dto': transferred_dto,
        'event': event
    })
