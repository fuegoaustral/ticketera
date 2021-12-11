from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.template import loader
from django.forms import modelformset_factory, BaseModelFormSet
from django.urls import reverse

from .models import Coupon, Order, TicketType, Ticket
from .forms import OrderForm, TicketForm


def home(request):

    coupon = Coupon.objects.filter(token=request.GET.get('coupon')).first()
    template = loader.get_template('tickets/home.html')
    context = {
        'ticket_types': TicketType.objects.filter(coupon=coupon),
    }

    return HttpResponse(template.render(context, request))


class BaseTicketFormset(BaseModelFormSet):
    def __init__(self, *args, **kwargs):
        super(BaseTicketFormset, self).__init__(*args, **kwargs)
        self.queryset = Ticket.objects.none()


def order(request, ticket_type_id):

    coupon = Coupon.objects.filter(token=request.GET.get('coupon')).first()
    try:
        ticket_type = TicketType.objects.get(id=ticket_type_id, coupon=coupon)
    except TicketType.DoesNotExist as e:
        return HttpResponse('No seas gato')


    order_form = OrderForm(request.POST or None)
    TicketsFormSet = modelformset_factory(Ticket, formset=BaseTicketFormset, extra=1,
                                          fields=('first_name', 'last_name', 'email', 'phone', 'dni'))
    tickets_formset = TicketsFormSet(request.POST or None)

    if request.method == 'POST':
        if order_form.is_valid() and tickets_formset.is_valid():
            order = order_form.save(commit=False)
            order.ticket_type = ticket_type
            tickets = tickets_formset.save(commit=False)
            order.amount = len(tickets) * ticket_type.price # + donations
            order.save()
            for ticket in tickets:
                ticket.order = order
                ticket.price = ticket_type.price_with_coupon if coupon else ticket_type.price
                ticket.save()

            return HttpResponseRedirect(redirect_to=reverse('order_detail', kwargs={'order_key': order.key}))

    else:
        order_form = OrderForm()
        tickets_formset = TicketsFormSet()

    template = loader.get_template('tickets/order_new.html')
    context = {
        'ticket_type': ticket_type,
        'order_form': order_form,
        'tickets_formset': tickets_formset,
    }

    return HttpResponse(template.render(context, request))


def order_detail(request, order_key):

    order = Order.objects.get(key=order_key)

    payment_preference_id = order.get_payment_preference()['id'] if order.status == Order.OrderStatus.PENDING else None

    template = loader.get_template('tickets/order_detail.html')
    context = {
        'order': order,
        'preference_id': payment_preference_id,
        'MERCADOPAGO_PUBLIC_KEY': settings.MERCADOPAGO['PUBLIC_KEY']
    }

    return HttpResponse(template.render(context, request))


def payment_success(request, order_key):
    order = Order.objects.get(key=order_key)
    order.status = Order.OrderStatus.CONFIRMED
    order.response = request.GET
    order.save()
    return HttpResponseRedirect(reverse('order_detail', kwargs={'order_key': order.key}))


def payment_failure(request):
    print('PAYMENT FAILURE')
    return HttpResponse('PAYMENT FAILURE')


def payment_pending(request):
    print('PAYMENT PENDING')
    return HttpResponse('PAYMENT PENDING')


def payment_notification(request):
    print('PAYMENT NOTIFICATION', request.POST)
    return HttpResponse('PAYMENT NOTIFICATION')

