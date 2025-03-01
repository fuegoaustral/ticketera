import uuid

import mercadopago
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render, redirect
from django.urls import reverse

from tickets.forms import CheckoutTicketSelectionForm, CheckoutDonationsForm
from tickets.models import Event, TicketType, Order, OrderTicket


@login_required
def select_tickets(request):
    if request.method == 'POST':
        form = CheckoutTicketSelectionForm(request.POST, user=request.user)
        if form.is_valid():
            request.session['ticket_selection'] = form.cleaned_data
            return redirect('select_donations')
        else:
            event = Event.objects.get(active=True)
            tickets_remaining = event.tickets_remaining() or 0
            available_tickets = event.max_tickets_per_order
            available_tickets = min(available_tickets, tickets_remaining)
            return render(request, 'checkout/select_tickets.html', {
                'form': form,
                'ticket_data': form.ticket_data,
                'available_tickets': available_tickets,
                'tickets_remaining': tickets_remaining
            })

    event = Event.objects.get(active=True)
    tickets_remaining = event.tickets_remaining() or 0
    available_tickets = event.max_tickets_per_order
    available_tickets = min(available_tickets, tickets_remaining)

    initial_data = request.session.get('ticket_selection', {})

    if 'new' in request.GET or request.session.get('order_sid') is None:
        request.session['order_sid'] = str(uuid.uuid4())
        request.session['event_id'] = event.id
        request.session.pop('ticket_selection', None)
        request.session.pop('donations', None)
        ticket_id = request.GET.get('ticket_id')
        if ticket_id:
            initial_data[f'ticket_{ticket_id}_quantity'] = 1

    form = CheckoutTicketSelectionForm(initial=initial_data)

    return render(request, 'checkout/select_tickets.html', {
        'form': form,
        'ticket_data': form.ticket_data,
        'available_tickets': available_tickets,
        'tickets_remaining': tickets_remaining
    })


@login_required
def select_donations(request):
    if request.method == 'POST':
        form = CheckoutDonationsForm(request.POST)
        if form.is_valid():
            request.session['donations'] = form.cleaned_data
            return redirect('order_summary')

    if 'new' in request.GET or request.session.get('order_sid') is None:
        request.session['order_sid'] = str(uuid.uuid4())
        request.session.pop('ticket_selection', None)
        request.session.pop('donations', None)

    initial_data = request.session.get('donations', {})
    form = CheckoutDonationsForm(initial=initial_data)

    return render(request, 'checkout/select_donations.html', {
        'form': form,
        'ticket_selection': request.session.get('ticket_selection', None),
    })


@login_required
def order_summary(request):
    if request.session.get('order_sid') is None:
        return redirect('select_tickets')

    ticket_selection = request.session.get('ticket_selection', {})
    donations = request.session.get('donations', {})
    event = Event.objects.get(active=True)

    total_amount = 0
    ticket_data = []
    items = []

    ticket_types = TicketType.objects.get_available_ticket_types_for_current_events()

    for ticket_type in ticket_types:
        field_name = f'ticket_{ticket_type.id}_quantity'
        quantity = ticket_selection.get(field_name, 0)
        price = ticket_type.price
        subtotal = price * quantity

        if quantity > 0:
            total_amount += subtotal
            ticket_data.append({
                'id': ticket_type.id,
                'name': ticket_type.name,
                'description': ticket_type.description,
                'price': price,
                'quantity': quantity,
                'subtotal': subtotal,
            })
            items.append({
                "id": ticket_type.name,
                "title": ticket_type.name,
                "description": ticket_type.description,
                "quantity": quantity,
                "unit_price": float(price),
            })

    donation_data = []

    for donation_type, donation_name in [('donation_art', 'Becas de Arte'), ('donation_venue', 'Donaciones a La Sede'),
                                         ('donation_grant', 'Beca Inclusión Radical')]:
        donation_amount = donations.get(donation_type, 0)
        if donation_amount > 0:
            total_amount += donation_amount
            donation_data.append({
                'id': donation_type,
                'name': donation_name,
                'quantity': 1,
                'subtotal': donation_amount,
            })
            items.append({
                "id": donation_type,
                "title": donation_name,
                "quantity": 1,
                "unit_price": donation_amount,
            })

    if request.method == 'POST':
        total_quantity = sum(item['quantity'] for item in ticket_data)
        remaining_event_tickets = event.tickets_remaining()

        if total_quantity > event.max_tickets_per_order:
            return HttpResponse('Superaste la cantidad máxima de tickets permitida.', status=401)

        if total_quantity > remaining_event_tickets:
            return HttpResponse('No hay suficientes tickets disponibles.', status=400)

        with transaction.atomic():
            order = Order(
                first_name=request.user.first_name,
                last_name=request.user.last_name,
                email=request.user.email,
                phone=request.user.profile.phone,
                dni=request.user.profile.document_number,
                amount=total_amount,
                status=Order.OrderStatus.PENDING,
                donation_art=donations.get('donation_art', 0),
                donation_venue=donations.get('donation_venue', 0),
                donation_grant=donations.get('donation_grant', 0),
                event=event,
                user=request.user,
                order_type=Order.OrderType.ONLINE_PURCHASE,
            )
            order.save()

            if ticket_types.exists():
                order_tickets = [
                    OrderTicket(
                        order=order,
                        ticket_type=ticket_type,
                        quantity=quantity
                    )
                    for ticket_type in ticket_types
                    if (quantity := ticket_selection.get(f'ticket_{ticket_type.id}_quantity', 0)) > 0
                ]
                if order_tickets:
                    OrderTicket.objects.bulk_create(order_tickets)

        preference_data = {
            "items": items,
            "payer": {
                "name": order.first_name,
                "surname": order.last_name,
                "email": order.email,
                "phone": {"number": order.phone},
                "identification": {"type": "DNI", "number": order.dni},
            },
            "back_urls": {
                "success": settings.APP_URL + reverse("checkout_payment_callback", kwargs={'order_key': order.key}),
                "failure": settings.APP_URL + reverse("order_summary"),
                "pending": settings.APP_URL + reverse("checkout_payment_callback", kwargs={'order_key': order.key}),
            },
            "auto_return": "approved",
            "statement_descriptor": event.name,
            "external_reference": str(order.key),
        }

        sdk = mercadopago.SDK(settings.MERCADOPAGO['ACCESS_TOKEN'])
        response = sdk.preference().create(preference_data)['response']

        order.response = response
        order.save()

        return HttpResponseRedirect(response['init_point'])

    return render(request, 'checkout/order_summary.html', {
        'ticket_data': ticket_data,
        'donation_data': donation_data,
        'total_amount': total_amount,
    })
