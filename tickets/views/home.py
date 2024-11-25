from django.http import HttpResponse
from django.template import loader

from events.models import Event
from tickets.models import Coupon, TicketType


def home(request):
    context = {}

    event = Event.objects.filter(active=True).first()

    if event:
        coupon = Coupon.objects.filter(token=request.GET.get('coupon'), ticket_type__event=event).first()
        ticket_types = TicketType.objects.get_available(coupon, event)

        if not ticket_types:
            next_ticket_type = TicketType.objects.get_next_ticket_type_available(event)
            context.update({
                'coupon': coupon,
                'next_ticket_type': next_ticket_type
            })
        context.update({
            'coupon': coupon,
            'ticket_types': ticket_types,
        })

    template = loader.get_template('tickets/home.html')
    return HttpResponse(template.render(context, request))


def ping(request):
    response = HttpResponse('pong 🏓')
    response['x-depreheader'] = 'tu vieja'
    return response
