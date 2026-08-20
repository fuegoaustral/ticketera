from django.db.models import Q

from tickets.models import NewTicket, Order, OrderTicket, Ticket

VOLUNTEER_ROLE_FIELDS = {
    'transmutator': 'volunteer_transmutator',
    'ranger': 'volunteer_ranger',
    'caos': 'volunteer_umpalumpa',
    'mad': 'volunteer_mad',
}


def _user_purchased_event_ids(user):
    confirmed = Order.objects.filter(status=Order.OrderStatus.CONFIRMED).filter(
        Q(user=user) | Q(email__iexact=user.email)
    )
    ids = set(
        confirmed.exclude(event_id__isnull=True).values_list('event_id', flat=True)
    )
    ids.update(
        OrderTicket.objects.filter(order__in=confirmed)
        .exclude(ticket_type__event_id__isnull=True)
        .values_list('ticket_type__event_id', flat=True)
    )
    return ids


def check_purchased_events(user, config):
    required = set(config.get('event_ids') or [])
    if not required:
        return False
    purchased = _user_purchased_event_ids(user)
    return required.issubset(purchased)


def check_volunteer_at_events(user, config):
    """
    True si el usuario es dueño de un bono con ese rol de voluntariado.
    Sin event_ids: cualquier evento (incluye futuros).
    Con event_ids: al menos uno de los listados.
    """
    field = VOLUNTEER_ROLE_FIELDS.get(config.get('role'))
    if not field:
        return False

    qs = NewTicket.objects.filter(owner=user, **{field: True})
    event_ids = config.get('event_ids') or []
    if event_ids:
        qs = qs.filter(event_id__in=event_ids)
    if config.get('must_be_used', False):
        qs = qs.filter(is_used=True)
    return qs.exists()


def _as_int_ids(values):
    ids = []
    for value in values or []:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return ids


def _user_legacy_ticket_event_ids(user, event_ids=None):
    """Bonos del modelo viejo (Ticket): Metanoia y ediciones anteriores."""
    qs = Ticket.objects.filter(
        Q(email__iexact=user.email)
        | Q(order__user=user)
        | Q(order__email__iexact=user.email),
        order__status=Order.OrderStatus.CONFIRMED,
    )
    found = set(
        qs.exclude(order__event_id__isnull=True).values_list('order__event_id', flat=True)
    )
    found.update(
        OrderTicket.objects.filter(order_id__in=qs.values('order_id'))
        .exclude(ticket_type__event_id__isnull=True)
        .values_list('ticket_type__event_id', flat=True)
    )
    if event_ids:
        found &= set(event_ids)
    return found


def _user_ticket_event_ids(user, event_ids=None, must_be_used=False):
    qs = NewTicket.objects.filter(Q(owner=user) | Q(holder=user))
    if event_ids:
        qs = qs.filter(event_id__in=event_ids)
    if must_be_used:
        qs = qs.filter(is_used=True)
    found = set(qs.exclude(event_id__isnull=True).values_list('event_id', flat=True))
    # El modelo viejo no tiene is_used; si pedimos escaneo, igual cuenta (no había scanner).
    found |= _user_legacy_ticket_event_ids(user, event_ids)
    return found


def check_attended_events(user, config):
    """
    True si el usuario participó en al menos min_count eventos de event_ids.
    Cuenta NewTicket (owner o holder) y Ticket legado (email en el bono u orden).
    must_be_used (default true) exige escaneo en NewTicket; los bonos viejos
    no tienen is_used y siempre cuentan. Si must_be_used es false, también
    cuenta órdenes CONFIRMED.
    """
    event_ids = set(_as_int_ids(config.get('event_ids') or []))
    try:
        min_count = int(config.get('min_count') or 0)
    except (TypeError, ValueError):
        return False
    if not event_ids or min_count < 1:
        return False

    must_be_used = config.get('must_be_used', True)
    attended = _user_ticket_event_ids(user, event_ids, must_be_used=must_be_used)
    if not must_be_used:
        attended |= {eid for eid in _user_purchased_event_ids(user) if eid in event_ids}
    return len(attended) >= min_count


CONDITION_CHECKERS = {
    'purchased_events': check_purchased_events,
    'volunteer_at_events': check_volunteer_at_events,
    'attended_events': check_attended_events,
}


def is_condition_met(user, achievement):
    checker = CONDITION_CHECKERS.get(achievement.condition_type)
    if not checker:
        return False
    return checker(user, achievement.condition_config or {})
