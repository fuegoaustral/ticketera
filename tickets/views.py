from datetime import datetime

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.template import loader
from django.forms import modelformset_factory, BaseModelFormSet
from django.urls import reverse

from deprepagos.email import send_mail
from .models import Coupon, Order, TicketType, Ticket
from .forms import OrderForm, TicketForm
from django.db.models import Count, Q, F


def home(request):

    coupon = Coupon.objects.filter(token=request.GET.get('coupon')).first()

    ticket_type = TicketType.objects\
        .annotate(confirmed_tickets=Count('order__ticket', filter=Q(order__status=Order.OrderStatus.CONFIRMED)))\
        .annotate(available_tickets=F('ticket_count') - F('confirmed_tickets'))\
        .filter(coupon=coupon)\
        .filter(Q(date_from__lte=datetime.now()) | Q(date_from__isnull=True))\
        .filter(Q(date_to__gte=datetime.now()) | Q(date_to__isnull=True))\
        .order_by('price' if coupon is None else '-price_with_coupon')\
        .first()

    template = loader.get_template('tickets/home.html')

    context = {
        'coupon': coupon,
        'ticket_type': ticket_type
    }

    return HttpResponse(template.render(context, request))


class BaseTicketFormset(BaseModelFormSet):
    def __init__(self, *args, **kwargs):
        super(BaseTicketFormset, self).__init__(*args, **kwargs)
        self.queryset = Ticket.objects.none()


def order(request, ticket_type_id):
    coupon = Coupon.objects.filter(token=request.GET.get('coupon')).first()

    try:
        ticket_type = TicketType.objects\
            .filter(id=ticket_type_id, coupon=coupon)\
            .filter(Q(date_from__lte=datetime.now()) | Q(date_from__isnull=True))\
            .filter(Q(date_to__gte=datetime.now()) | Q(date_to__isnull=True))\
            .get()
    except TicketType.DoesNotExist as e:
        return HttpResponse('Lo sentimos, este link es inválido.', status=404)

    max_tickets = coupon.max_tickets if coupon is not None else 5

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
            tickets = tickets_formset.save(commit=False)
            price = ticket_type.price_with_coupon if order.coupon else ticket_type.price
            order.amount = len(tickets) * price # + donations
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


def order_detail(request, order_key):

    order = Order.objects.get(key=order_key)

    context = {
        'order': order,
    }

    if order.amount < 0:

        payment_preference_id = order.get_payment_preference()['id'] if order.status == Order.OrderStatus.PENDING else None

        context = {
            'preference_id': payment_preference_id,
            'MERCADOPAGO_PUBLIC_KEY': settings.MERCADOPAGO['PUBLIC_KEY']
        }

    template = loader.get_template('tickets/order_detail.html')

    return HttpResponse(template.render(context, request))


def ticket_detail(request, ticket_key):

    ticket = Ticket.objects.get(key=ticket_key)

    template = loader.get_template('tickets/ticket_detail.html')
    context = {
        'ticket': ticket,
    }

    return HttpResponse(template.render(context, request))


def complete_order(order):
    order.status = Order.OrderStatus.CONFIRMED
    order.save()

    order_url = reverse('order_detail', kwargs={'order_key': order.key})

    send_mail(
        template_name='order_success',
        recipient_list=[settings.DEFAULT_FROM_EMAIL, order.email],
        context={
            'order': order,
            'url': order_url
        }
    )

    for ticket in order.ticket_set.all():
        ticket.send_email()

    return HttpResponseRedirect(order_url)


def free_order_confirmation(request, order_key):
    order = Order.objects.get(key=order_key)
    if order.amount > 0:
        raise HttpResponseBadRequest('This order cannot be confirmed without payment')

    return complete_order(order)


def payment_success(request, order_key):
    # TODO: add some kind of security
    order = Order.objects.get(key=order_key)
    order.response = request.GET

    return complete_order(order)


def payment_failure(request):
    print('PAYMENT FAILURE')
    return HttpResponse('PAYMENT FAILURE')


def payment_pending(request):
    print('PAYMENT PENDING')
    return HttpResponse('PAYMENT PENDING')


def payment_notification(request):
    print('PAYMENT NOTIFICATION', request.POST)
    return HttpResponse('PAYMENT NOTIFICATION')

