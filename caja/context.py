from events.models import Event


def mi_fuego_admin_context(request, event, nav_secondary):
    return {
        'event': event,
        'main_event': Event.get_main_event(),
        'admin_events': Event.objects.filter(admins=request.user).order_by('-is_main', 'name'),
        'active_events': Event.get_active_events().filter(
            newticket__holder=request.user,
        ).distinct().order_by('-is_main', 'name'),
        'nav_primary': 'events',
        'nav_secondary': nav_secondary,
    }
