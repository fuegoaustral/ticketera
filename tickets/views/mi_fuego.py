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
    tickets = NewTicket.objects.filter(holder=request.user, event=event)

    tickets_dto = []

    for ticket in tickets:
        transfer_pending = NewTicketTransfer.objects.filter(ticket=ticket, tx_from=request.user,
                                                            status='PENDING').first()
        tickets_dto.append({
            'key': ticket.key,
            'order': ticket.order.key,
            'ticket_type': ticket.ticket_type.name,
            'ticket_color': ticket.ticket_type.color,
            'emoji': ticket.ticket_type.emoji,
            'price': ticket.ticket_type.price,
            'is_transfer_pending': transfer_pending is not None,
            'transferring_to': transfer_pending.tx_to_email if transfer_pending else None,
            'is_owners': ticket.holder == ticket.owner,
            'volunteer_ranger': ticket.volunteer_ranger,
            'volunteer_transmutator': ticket.volunteer_transmutator,
            'volunteer_umpalumpa': ticket.volunteer_umpalumpa,
            'qr_code': ticket.generate_qr_code(),
        })
    tickets_dto = sorted(tickets_dto, key=lambda x: not x['is_owners'])

    # Check if any ticket is not owned by the current user
    has_unassigned_tickets = any(ticket['is_owners'] is False for ticket in tickets_dto)

    has_assigned_tickets = any(ticket['is_owners'] is True for ticket in tickets_dto)

    is_volunteer = any(ticket['is_owners'] is True and (
            ticket['volunteer_ranger'] or ticket['volunteer_transmutator'] or ticket['volunteer_umpalumpa']) for
                       ticket in tickets_dto)

    # Check if any ticket has a transfer pending
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
        'is_volunteer': is_volunteer,
        'has_assigned_tickets': has_assigned_tickets,
        'has_unassigned_tickets': has_unassigned_tickets,
        'has_transfer_pending': has_transfer_pending,
        'tickets_dto': tickets_dto,
        'transferred_dto': transferred_dto,
        'event': event
    })
