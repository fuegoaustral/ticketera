from events.models import Event
from events.utils import get_admin_events_for_user


def mi_fuego_admin_context(request, event, nav_secondary):
    return {
        'event': event,
        'current_admin_event': event,
        'main_event': Event.get_main_event(),
        'admin_events': get_admin_events_for_user(request.user),
        'active_events': Event.get_active_events().filter(
            newticket__holder=request.user,
        ).distinct().order_by('-is_main', 'name'),
        'nav_primary': 'events',
        'nav_secondary': nav_secondary,
    }
