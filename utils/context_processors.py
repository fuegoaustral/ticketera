from events.models import Event

def current_event(request):
    try:
        event = Event.objects.get(active=True)
    except Event.DoesNotExist:
        event = Event.objects.latest('id')
    return {
        'event': event,
    }

