from django.http import HttpResponse, HttpResponseRedirect, HttpResponseNotFound, Http404
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

def public_ticket_detail(request, ticket_key, event_slug=None):
    try:
        ticket = NewTicket.objects.get(key=ticket_key)
        if event_slug:
            current_event = Event.get_by_slug(event_slug)
        else:
            current_event = Event.get_main_event()
        
        is_valid = (
            not ticket.is_used and
            ticket.event.active
        )
        
        # Get ticket DTO for QR code
        ticket_dto = ticket.get_dto(user=None)
        
        context = {
            'ticket': ticket,
            'ticket_dto': ticket_dto,  # Add DTO for QR code
            'event': ticket.event,
            'is_valid': is_valid,
        }
        return render(request, 'mi_fuego/tickets/public_ticket.html', context)
    except NewTicket.DoesNotExist:
        raise Http404("Ticket no encontrado")
