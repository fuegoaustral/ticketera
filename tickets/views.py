from django.http import HttpResponse
from django.template import loader

from .models import TicketType


def home(request):
    ticket_types = TicketType.objects.all()
    template = loader.get_template('tickets/home.html')
    context = {
        'ticket_types': ticket_types,
    }
    return HttpResponse(template.render(context, request))