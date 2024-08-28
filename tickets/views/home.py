from django.http import HttpResponse
from django.template import loader

from tickets.crons import  send_pending_actions_emails
from tickets.models import Coupon, TicketType
from events.models import Event


def home(request):
    context = {}

    event = Event.objects.filter(active=True).first()

    if event:
        coupon = Coupon.objects.filter(token=request.GET.get('coupon'), ticket_type__event=event).first()
        ticket_types = TicketType.objects.get_available(coupon, event)
        context.update({
            'coupon': coupon,
            'ticket_types': ticket_types
        })

    template = loader.get_template('tickets/home.html')
    return HttpResponse(template.render(context, request))


def test(request):
    send_pending_actions_emails(None, None)

    return HttpResponse('Test executed')
