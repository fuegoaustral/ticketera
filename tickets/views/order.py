import logging

from django.forms import modelformset_factory, BaseModelFormSet
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse, HttpResponseForbidden, HttpResponseBadRequest
from django.template import loader
from django.urls import reverse
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
import mercadopago
from django.conf import settings

from events.models import Event
from tickets.models import Order, TicketType, OrderTicket, Coupon, Ticket
from tickets.forms import OrderForm, CheckoutTicketSelectionForm, CheckoutDonationsForm, TicketForm

from .utils import is_order_valid, _complete_order

class BaseTicketFormset(BaseModelFormSet):
    def __init__(self, *args, **kwargs):
        super(BaseTicketFormset, self).__init__(*args, **kwargs)
        self.queryset = Ticket.objects.none()
def order(request, ticket_type_id):
    try:
        event = Event.objects.get(active=True)
    except Event.DoesNotExist:
        return HttpResponse('Lo sentimos, este link es inválido.', status=404)

    coupon = Coupon.objects.filter(token=request.GET.get('coupon')).first()
    ticket_types = TicketType.objects.get_available(coupon, event)

    for ticket_type in ticket_types:
        if ticket_type.pk == ticket_type_id:
            break
    if ticket_type.pk != ticket_type_id:
        return HttpResponse('Lo sentimos, este link es inválido.', status=404)

    max_tickets = min(ticket_type.available_tickets, coupon.tickets_remaining() if coupon else 5, event.tickets_remaining())
    order_form = OrderForm(request.POST or None)
    TicketsFormSet = modelformset_factory(Ticket, formset=BaseTicketFormset, form=TicketForm,
                                          max_num=max_tickets, validate_max=True, min_num=1, validate_min=True,
                                          extra=0)
    tickets_formset = TicketsFormSet(request.POST or None)

    if request.method == 'POST':
        if order_form.is_valid() and tickets_formset.is_valid():
            order = order_form.save(commit=False)
            if coupon:
                order.coupon = coupon
            order.ticket_type = ticket_type
            order.coupon = coupon
            tickets = tickets_formset.save(commit=False)
            price = ticket_type.price_with_coupon if order.coupon else ticket_type.price
            order.amount = len(tickets) * price
            order.amount += order.donation_art or 0
            order.amount += order.donation_grant or 0
            order.amount += order.donation_venue or 0
            order.save()
            for ticket in tickets:
                ticket.order = order
                ticket.price = price
                ticket.save()

            return HttpResponseRedirect(redirect_to=reverse('order_detail', kwargs={'order_key': order.key}))

    template = loader.get_template('tickets/order_new.html')
    context = {
        'max_tickets': max_tickets,
        'ticket_type': ticket_type,
        'order_form': order_form,
        'coupon': coupon,
        'tickets_formset': tickets_formset,
    }

    return HttpResponse(template.render(context, request))

def order_detail(request, order_key):
    order = Order.objects.get(key=order_key)
    logging.info('got order')

    context = {
        'order': order,
        'event': order.ticket_type.event,
        'is_order_valid': (order.status == 'CONFIRMED') or is_order_valid(order),
    }
    logging.info('got order context')

    if order.amount > 0:
        logging.info('getting payment preferences')
        payment_preference_id = order.get_payment_preference()['id'] if order.status == Order.OrderStatus.PENDING else None

        context.update({
            'preference_id': payment_preference_id,
            'MERCADOPAGO_PUBLIC_KEY': settings.MERCADOPAGO['PUBLIC_KEY'],
        })
    logging.info('got payment preferences')

    template = loader.get_template('tickets/order_detail.html')
    logging.info('got template')

    rendered_template = template.render(context, request)
    logging.info('rendered template')

    return HttpResponse(rendered_template)

def free_order_confirmation(request, order_key):
    order = Order.objects.get(key=order_key)

    if not is_order_valid(order):
        return HttpResponse('Lo sentimos, este link es inválido.', status=404)

    if order.amount > 0:
        raise HttpResponseBadRequest('This order cannot be confirmed without payment')

    return _complete_order(order)

def payment_success(request, order_key):
    order = Order.objects.get(key=order_key)
    order.response = request.GET
    return HttpResponseRedirect(order.get_resource_url())

def payment_failure(request):
    return HttpResponse('PAYMENT FAILURE')

def payment_pending(request):
    return HttpResponse('PAYMENT PENDING')

@csrf_exempt
def payment_notification(request):
    if request.GET['topic'] == 'payment':
        sdk = mercadopago.SDK(settings.MERCADOPAGO['ACCESS_TOKEN'])
        payment = sdk.payment().get(request.GET.get('id'))['response']

        merchant_order = sdk.merchant_order().get(payment['order']['id'])['response']

        order = Order.objects.get(id=int(merchant_order['external_reference']))

        paid_amount = 0
        for payment in merchant_order['payments']:
            if payment['status'] == 'approved':
                paid_amount += payment['total_paid_amount']

        logging.info('paid amount: %s', paid_amount)
        if paid_amount >= merchant_order['total_amount']:
            logging.info('order is paid')
            _complete_order(order)
            logging.info('order completed')

    return HttpResponse('Notified!')

@login_required
def check_order_status(request, order_key):
    order = Order.objects.get(key=order_key)
    if order.email != request.user.email:
        return HttpResponseForbidden('Forbidden')
    return JsonResponse({"status": order.status})

@login_required
def checkout_payment_callback(request, order_key):
    request.session.pop('ticket_selection', None)
    request.session.pop('donations', None)
    request.session.pop('order_sid', None)

    order = Order.objects.get(key=order_key)
    if order.email != request.user.email:
        return HttpResponseForbidden('Forbidden')

    return render(request, 'checkout/payment_callback.html', {
        'order_key': order_key,
    })
