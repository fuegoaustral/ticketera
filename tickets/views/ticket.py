from django.http import HttpResponse, HttpResponseRedirect, HttpResponseNotFound
from django.template import loader
from django.urls import reverse
from django.shortcuts import get_object_or_404, render
from tickets.models import Ticket, TicketTransfer, NewTicket
from tickets.forms import TransferForm
from django.utils.timezone import now
from events.models import Event
from django.utils import timezone

def ticket_detail(request, ticket_key):
    ticket = Ticket.objects.get(key=ticket_key)
    template = loader.get_template('tickets/ticket_detail.html')
    context = {
        'ticket': ticket,
        'event': ticket.order.ticket_type.event,
    }
    return HttpResponse(template.render(context, request))

def ticket_transfer(request, ticket_key):
    ticket = Ticket.objects.select_related('order__ticket_type__event').get(key=ticket_key)

    if ticket.order.ticket_type.event.transfers_enabled_until < now():
        template = loader.get_template('tickets/ticket_transfer_expired.html')
        return HttpResponse(template.render({'ticket': ticket}, request))

    if request.method == 'POST':
        form = TransferForm(request.POST)
        if form.is_valid():
            transfer = form.save(commit=False)
            transfer.ticket = ticket
            transfer.volunteer_ranger = False
            transfer.volunteer_transmutator = False
            transfer.volunteer_umpalumpa = False
            transfer.save()
            transfer.send_email()

            return HttpResponseRedirect(reverse('ticket_transfer_confirmation', args=[ticket.key]))
    else:
        form = TransferForm()

    template = loader.get_template('tickets/ticket_transfer.html')
    context = {
        'ticket': ticket,
        'event': ticket.order.ticket_type.event,
        'form': form,
    }

    return HttpResponse(template.render(context, request))

def ticket_transfer_confirmation(request, ticket_key):
    ticket = Ticket.objects.get(key=ticket_key)

    template = loader.get_template('tickets/ticket_transfer_confirmation.html')
    context = {
        'ticket': ticket,
    }

    return HttpResponse(template.render(context, request))

def ticket_transfer_confirmed(request, transfer_key):
    transfer = TicketTransfer.objects.get(key=transfer_key)

    transfer.transfer()

    template = loader.get_template('tickets/ticket_transfer_confirmed.html')
    context = {
        'transfer': transfer,
    }

    return HttpResponse(template.render(context, request))

def public_ticket_detail(request, ticket_key):
    ticket = get_object_or_404(NewTicket, key=ticket_key)
    
    # Only show public view for guest tickets
    if ticket.owner == ticket.holder:
        return HttpResponseNotFound('Ticket not found')
        
    current_event = Event.objects.filter(active=True).first()
    
    # Check if ticket is valid and belongs to current event
    is_valid = (
        current_event is not None and
        ticket.ticket_type.event == current_event and
        not ticket.is_used and
        ticket.ticket_type.event.end >= timezone.now()
    )
    
    # Get ticket DTO
    ticket_dto = ticket.get_dto(user=None)
    
    # Add tag to distinguish between Mine and Guest tickets
    ticket_dto['tag'] = 'Guest'
    
    # Safely get holder information
    holder_name = None
    holder_dni = None
    if ticket.holder:
        holder_name = f"{ticket.holder.first_name} {ticket.holder.last_name}"
        if hasattr(ticket.holder, 'profile') and ticket.holder.profile:
            holder_dni = ticket.holder.profile.document_number
    
    print(f"Ticket {ticket_key} validation:")
    print(f"- Current event exists: {current_event is not None}")
    print(f"- Ticket event matches: {ticket.ticket_type.event == current_event}")
    print(f"- Ticket not used: {not ticket.is_used}")
    print(f"- Event not ended: {ticket.ticket_type.event.end >= timezone.now()}")
    print(f"- Final is_valid: {is_valid}")
    
    
    context = {
        'ticket': ticket_dto,
        'event': current_event,
        'is_valid': is_valid,
        'holder_name': holder_name,
        'holder_dni': holder_dni,
    }
    
    return render(request, 'mi_fuego/tickets/public_ticket.html', context)
