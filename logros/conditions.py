from django.db.models import Q

from tickets.models import NewTicket, Order

VOLUNTEER_ROLE_FIELDS = {
    'transmutator': 'volunteer_transmutator',
    'ranger': 'volunteer_ranger',
    'caos': 'volunteer_umpalumpa',
    'mad': 'volunteer_mad',
}


def _user_purchased_event_ids(user):
    return set(
        Order.objects.filter(status=Order.OrderStatus.CONFIRMED)
        .filter(Q(user=user) | Q(email__iexact=user.email))
        .exclude(event_id__isnull=True)
        .values_list('event_id', flat=True)
        .distinct()
    )


def check_purchased_events(user, config):
    required = set(config.get('event_ids') or [])
    if not required:
        return False
    purchased = _user_purchased_event_ids(user)
    return required.issubset(purchased)


def check_volunteer_at_events(user, config):
    """
    True si el usuario es dueño de un bono con ese rol de voluntariado
    en al menos uno de los event_ids (un FA pasado, no todos).
    """
    event_ids = config.get('event_ids') or []
    field = VOLUNTEER_ROLE_FIELDS.get(config.get('role'))
    if not event_ids or not field:
        return False

    qs = NewTicket.objects.filter(
        owner=user,
        event_id__in=event_ids,
        **{field: True},
    )
    if config.get('must_be_used', False):
        qs = qs.filter(is_used=True)
    return qs.exists()


CONDITION_CHECKERS = {
    'purchased_events': check_purchased_events,
    'volunteer_at_events': check_volunteer_at_events,
}


def is_condition_met(user, achievement):
    checker = CONDITION_CHECKERS.get(achievement.condition_type)
    if not checker:
        return False
    return checker(user, achievement.condition_config or {})
