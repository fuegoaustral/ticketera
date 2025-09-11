from django.http import HttpResponse
from django.template import loader

from events.models import Event
from events.utils import get_event_from_request
from tickets.models import Coupon, TicketType


def home(request, event_slug=None):
    context = {}

    try:
        # Get event from URL slug or fallback to main event
        if event_slug:
            event = Event.get_by_slug(event_slug)
            if not event:
                return HttpResponse('Event not found', status=404)
        else:
            # For the main page (/), always show the main event
            event = Event.get_main_event()
    except Exception:
        return HttpResponse('No active events found', status=404)

    if event:
        coupon = Coupon.objects.filter(token=request.GET.get('coupon'), ticket_type__event=event).first()
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

        if not ticket_types:
            next_ticket_type = TicketType.objects.get_next_ticket_type_available(event)
            context.update({
                'coupon': coupon,
                'next_ticket_type': next_ticket_type
            })
        # Check if there are multiple active events
        active_events = Event.get_active_events()
        has_multiple_events = active_events.count() > 1
        
        context.update({
            'coupon': coupon,
            'ticket_types': ticket_types,
            'current_event': event,  # Add current event to context
            'has_multiple_events': has_multiple_events,  # Add flag for multiple events
        })

    template = loader.get_template('tickets/home.html')
    return HttpResponse(template.render(context, request))


def events_listing(request):
    """List all active events"""
    from django.shortcuts import render
    
    # Get all active events
    active_events = Event.get_active_events()
    
    context = {
        'active_events': active_events,
    }
    
    return render(request, 'tickets/events_listing.html', context)


def ping(request):
    response = HttpResponse('pong 🏓')
    response['x-depreheader'] = 'tu vieja'
    return response
