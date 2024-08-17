from datetime import datetime

import mercadopago, logging

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect, HttpResponseBadRequest
from django.template import loader
from django.forms import modelformset_factory, BaseModelFormSet
from django.urls import reverse
from django.utils.timezone import now
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect

from .models import Coupon, Order, TicketType, Ticket, TicketTransfer
from .forms import OrderForm, TicketForm, TransferForm, ProfileStep1Form, ProfileStep2Form
from events.models import Event


def home(request):
    try:
        event = Event.objects.get(active=True)
    except Event.DoesNotExist:
        event = None

    context = {}

    if event:
        coupon = Coupon.objects.filter(token=request.GET.get('coupon'), ticket_type__event=event).first()

        ticket_types = TicketType.objects.get_available(coupon, event)

        context.update({
            'coupon': coupon,
            'ticket_types': ticket_types
        })

    template = loader.get_template('tickets/home.html')
    return HttpResponse(template.render(context, request))


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

    # we need to iterate over because after slicing we cannot filter
    for ticket_type in ticket_types:
        if ticket_type.pk == ticket_type_id:
            break
    if ticket_type.pk != ticket_type_id:
        return HttpResponse('Lo sentimos, este link es inválido.', status=404)

    # get available tickets from coupon/type
    max_tickets = min(ticket_type.available_tickets, coupon.tickets_remaining() if coupon else 5,
                      event.tickets_remaining())

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
            order.amount = len(tickets) * price  # + donations
            order.amount += order.donation_art or 0
            order.amount += order.donation_grant or 0
            order.amount += order.donation_venue or 0
            order.save()
            for ticket in tickets:
                ticket.order = order
                ticket.price = price
                ticket.save()

            return HttpResponseRedirect(redirect_to=reverse('order_detail', kwargs={'order_key': order.key}))

    else:
        order_form = OrderForm()
        tickets_formset = TicketsFormSet()

    template = loader.get_template('tickets/order_new.html')
    context = {
        'max_tickets': max_tickets,
        'ticket_type': ticket_type,
        'order_form': order_form,
        'coupon': coupon,
        'tickets_formset': tickets_formset,
    }

    return HttpResponse(template.render(context, request))


def is_order_valid(order):
    num_tickets = order.ticket_set.count()

    ticket_types = TicketType.objects.get_available(order.coupon, order.ticket_type.event)

    try:
        # use the get_available method that annotates the queryset with available_tickets
        ticket_type = ticket_types.get(pk=order.ticket_type.pk)
    except TicketType.DoesNotExist:
        return False

    if ticket_type.available_tickets < num_tickets:
        return False

    if order.coupon and order.coupon.tickets_remaining() < num_tickets:
        return False

    if ticket_type.event.tickets_remaining() < num_tickets:
        return False
    return True


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
        payment_preference_id = order.get_payment_preference()[
            'id'] if order.status == Order.OrderStatus.PENDING else None

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


def _complete_order(order):
    logging.info('completing order')
    order.status = Order.OrderStatus.CONFIRMED
    logging.info('saving order')
    order.save()
    logging.info('redirecting')
    return HttpResponseRedirect(order.get_resource_url())


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
def dashboard_view(request):
    # Add logic to handle the dashboard view
    return render(request, 'dashboard/protected_page.html')


@login_required
def complete_profile(request):
    profile = request.user.profile
    error_message = None
    code_sent = False

    if profile.profile_completion == 'NONE':
        if request.method == 'POST':
            form = ProfileStep1Form(request.POST, instance=profile, user=request.user)
            if form.is_valid():
                form.save()
                profile.profile_completion = 'INITIAL_STEP'
                profile.save()
                return redirect('complete_profile')
        else:
            form = ProfileStep1Form(instance=profile, user=request.user)
        return render(request, 'account/complete_profile_step1.html', {'form': form})

    elif profile.profile_completion == 'INITIAL_STEP':
        form = ProfileStep2Form(request.POST or None, instance=profile)
        if request.method == 'POST':
            if 'send_code' in request.POST:
                if form.is_valid():
                    form.save()
                    form.send_verification_code()
                    code_sent = True
            elif 'verify_code' in request.POST:
                code_sent = True  # Keep this True to stay in verification mode
                form = ProfileStep2Form(request.POST, instance=profile, code_sent=True)
                if form.is_valid():
                    if form.verify_code():
                        profile.profile_completion = 'COMPLETE'
                        profile.save()
                        return redirect('home')
                    else:
                        error_message = "Código inválido. Por favor, intenta de nuevo."

        return render(request, 'account/complete_profile_step2.html', {
            'form': form,
            'error_message': error_message,
            'code_sent': code_sent,
            'profile': profile  # Pass the profile object to the template
        })
    else:
        return redirect('home')


def verification_congrats(request):
    return render(request, 'account/verification_congrats.html')