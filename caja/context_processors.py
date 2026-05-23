from collections import defaultdict

from caja.models import EventCaja
from events.models import Event


def cajas_v2_menu(request):
    if not request.user.is_authenticated:
        return {}
    event_ids = Event.objects.filter(admins=request.user).values_list('id', flat=True)
    by_event_id = defaultdict(list)
    for caja in (
        EventCaja.objects.filter(event_id__in=event_ids, is_active=True)
        .order_by('sort_order', 'name', 'id')
    ):
        by_event_id[caja.event_id].append(caja)
    return {'cajas_v2_by_event_id': dict(by_event_id)}
