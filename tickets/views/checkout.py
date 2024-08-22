import uuid

import mercadopago
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.urls import reverse

from tickets.models import Event, TicketType, Order, OrderTicket
from tickets.forms import CheckoutTicketSelectionForm, CheckoutDonationsForm
from .utils import available_tickets_for_user

@login_required
def select_tickets(request):
    event = Event.objects.get(active=True)
    tickets_remaining = event.tickets_remaining() or 0
    available_tickets = available_tickets_for_user(request.user) or 0

    if available_tickets > tickets_remaining:
        available_tickets = tickets_remaining

    if 'new' in request.GET or request.session.get('order_sid') is None:
        request.session['order_sid'] = str(uuid.uuid4())
        request.session['event_id'] = event.id
        request.session.pop('ticket_selection', None)
        request.session.pop('donations', None)

    if request.method == 'POST':
        form = CheckoutTicketSelectionForm(request.POST)
        if form.is_valid():
            request.session['ticket_selection'] = form.cleaned_data
            return redirect('select_donations')
    else:
        initial_data = request.session.get('ticket_selection', {})
        form = CheckoutTicketSelectionForm(initial=initial_data)

    return render(request, 'checkout/select_tickets.html', {
        'form': form,
        'ticket_data': form.ticket_data,
        'available_tickets': available_tickets,
        'tickets_remaining': tickets_remaining
    })

@login_required
def select_donations(request):
    if 'new' in request.GET or request.session.get('order_sid') is None:
        request.session['order_sid'] = str(uuid.uuid4())
        request.session.pop('ticket_selection', None)
        request.session.pop('donations', None)

    if request.method == 'POST':
        form = CheckoutDonationsForm(request.POST)
        if form.is_valid():
            request.session['donations'] = form.cleaned_data
            return redirect('order_summary')
    else:
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

    donation_amount = settings.DONATION_AMOUNT
    donation_data = []

    for donation_type, donation_name in [('donation_art', 'Becas de Arte'), ('donation_venue', 'Donaciones a La Sede'),
                                         ('donation_grant', 'Beca Inclusión Radical')]:
        quantity = donations.get(donation_type, 0)
        subtotal = quantity * donation_amount
        if quantity > 0:
            total_amount += subtotal
            donation_data.append({
                'id': donation_type,
                'name': donation_name,
                'quantity': quantity,
                'subtotal': subtotal,
            })
            items.append({
                "id": donation_type,
                "title": donation_name,
                "quantity": quantity,
                "unit_price": float(donation_amount),
            })

    if request.method == 'POST':
        available_tickets = available_tickets_for_user(request.user)
        total_quantity = sum(item['quantity'] for item in ticket_data)
        remaiining_event_tickets = event.tickets_remaining()

        if total_quantity > available_tickets:
            return HttpResponse('Ya compraste la cantidad máxima de tickets permitida.', status=400)

        if total_quantity > remaiining_event_tickets:
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
                donation_art=donations.get('donation_art', 0) * donation_amount,
                donation_venue=donations.get('donation_venue', 0) * donation_amount,
                donation_grant=donations.get('donation_grant', 0) * donation_amount,
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
            "payment_methods": {
                "excluded_payment_types": [
                    {
                        "id": "ticket"
                    }
                ],
            },
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
