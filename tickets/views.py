from django.http import HttpResponse
from django.template import loader

from .models import Coupon, TicketType


def home(request):

    coupon = Coupon.objects.filter(token=request.GET.get('coupon')).first()

    ticket_types = TicketType.objects.filter(coupon=coupon)

    template = loader.get_template('tickets/home.html')
    context = {
        'ticket_types': ticket_types,
    }

    return HttpResponse(template.render(context, request))