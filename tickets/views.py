from django.http import HttpResponse
from django.template import loader
from django.forms import modelformset_factory

from .models import Coupon, TicketType, Ticket
from .forms import OrderForm, TicketForm


def home(request):

    coupon = Coupon.objects.filter(token=request.GET.get('coupon')).first()
    template = loader.get_template('tickets/home.html')
    context = {
        'ticket_types': TicketType.objects.filter(coupon=coupon),
    }

    return HttpResponse(template.render(context, request))


def order(request, ticket_type_id):

    coupon = Coupon.objects.filter(token=request.GET.get('coupon')).first()
    try:
        ticket_type = TicketType.objects.get(id=ticket_type_id, coupon=coupon)
    except TicketType.DoesNotExist as e:
        return HttpResponse('No seas gato')

    template = loader.get_template('tickets/order.html')

    TicketsFormSet = modelformset_factory(Ticket, exclude=(), extra=4)

    if request.method == 'POST':
        print(request.POST)
        order_form = OrderForm(request.POST)
        tickets_formset = TicketsFormSet(request.POST)
        if order_form.is_valid() and tickets_formset.is_valid():
            print('SAVE RECORD', request.POST)
    else:
        order_form = OrderForm()
        tickets_formset = TicketsFormSet()

    context = {
        'ticket_type': ticket_type,
        'order_form': order_form,
        'tickets_formset': tickets_formset,
    }

    return HttpResponse(template.render(context, request))


