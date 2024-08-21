from django.http import HttpResponse, HttpResponseRedirect
from django.template import loader
from django.urls import reverse
from django.shortcuts import get_object_or_404
from tickets.models import Ticket, TicketTransfer
from tickets.forms import TransferForm
from django.utils.timezone import now

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
