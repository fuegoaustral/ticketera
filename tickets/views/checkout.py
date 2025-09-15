import uuid

import mercadopago
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render, redirect
from django.urls import reverse

from events.models import Event
from events.utils import get_event_from_request, store_event_in_session
from tickets.forms import CheckoutTicketSelectionForm, CheckoutDonationsForm
from tickets.models import TicketType, Order, OrderTicket


@login_required
def select_tickets(request, event_slug=None):
    # Get event from URL slug or request
    if event_slug:
        event = Event.get_by_slug(event_slug)
        if not event:
            return HttpResponse('Event not found', status=404)
    else:
        event = get_event_from_request(request)
    
    # Store event in session for checkout flow
    store_event_in_session(request, event)
    
    if request.method == 'POST':
        form = CheckoutTicketSelectionForm(request.POST, user=request.user, event=event)
        if form.is_valid():
            request.session['ticket_selection'] = form.cleaned_data
            # Redirect with event parameter
            if event.slug:
                return redirect(f"{reverse('select_donations')}?event={event.slug}")
            return redirect('select_donations')
        else:
            tickets_remaining = event.tickets_remaining() or 0
            available_tickets = event.max_tickets_per_order
            available_tickets = min(available_tickets, tickets_remaining)
            return render(request, 'checkout/select_tickets.html', {
                'form': form,
                'ticket_data': form.ticket_data,
                'available_tickets': available_tickets,
                'tickets_remaining': tickets_remaining,
                'current_event': event,
            })

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

    form = CheckoutTicketSelectionForm(initial=initial_data, event=event)

    return render(request, 'checkout/select_tickets.html', {
        'form': form,
        'ticket_data': form.ticket_data,
        'available_tickets': available_tickets,
        'tickets_remaining': tickets_remaining,
        'current_event': event,
    })


@login_required
def select_donations(request, event_slug=None):
    # Get event from session or request
    event = get_event_from_request(request)
    
    if request.method == 'POST':
        form = CheckoutDonationsForm(request.POST)
        if form.is_valid():
            request.session['donations'] = form.cleaned_data
            # Redirect with event parameter
            if event.slug:
                return redirect(f"{reverse('order_summary')}?event={event.slug}")
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
        'current_event': event,
    })


@login_required
def order_summary(request, event_slug=None):
    if request.session.get('order_sid') is None:
        if event.slug:
            return redirect(f"{reverse('select_tickets')}?event={event.slug}")
        return redirect('select_tickets')

    ticket_selection = request.session.get('ticket_selection', {})
    donations = request.session.get('donations', {})
    event = get_event_from_request(request)

    total_amount = 0
    ticket_data = []
    items = []

    # Filter ticket types by the specific event using the same logic as get_available_ticket_types_for_current_events
    from django.utils import timezone
    from django.db.models import Q
    ticket_types = (TicketType.objects
                  .filter(event=event)
                  .filter(Q(date_from__lte=timezone.now()) | Q(date_from__isnull=True))
                  .filter(Q(date_to__gte=timezone.now()) | Q(date_to__isnull=True))
                  .filter(Q(ticket_count__gt=0) | Q(ticket_count__isnull=True))
                  .filter(is_direct_type=False)
                  .order_by('cardinality', 'price'))

    for ticket_type in ticket_types:
        field_name = f'ticket_{ticket_type.id}_quantity'
        quantity = ticket_selection.get(field_name, 0)
        price = ticket_type.price
        
        # For free tickets (price = 0), use custom amount
        if price == 0:
            custom_amount_field = f'ticket_{ticket_type.id}_custom_amount'
            custom_amount = ticket_selection.get(custom_amount_field, 0)
            subtotal = custom_amount * quantity
            effective_price = custom_amount
        else:
            subtotal = price * quantity
            effective_price = price

        if quantity > 0:
            total_amount += subtotal
            ticket_data.append({
                'id': ticket_type.id,
                'name': ticket_type.name,
                'description': ticket_type.description,
                'price': effective_price,
                'quantity': quantity,
                'subtotal': subtotal,
                'is_free_ticket': price == 0,
                'original_price': price,
            })
            items.append({
                "id": ticket_type.name,
                "title": ticket_type.name,
                "description": ticket_type.description,
                "quantity": quantity,
                "unit_price": float(effective_price),
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

        # Check if any ticket type ignores max amount
        has_ignore_max_amount = any(
            ticket_type.ignore_max_amount 
            for ticket_type in ticket_types 
            if ticket_selection.get(f'ticket_{ticket_type.id}_quantity', 0) > 0
        )

        # Only check remaining event tickets if no ticket type ignores max amount
        if not has_ignore_max_amount and total_quantity > remaining_event_tickets:
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
