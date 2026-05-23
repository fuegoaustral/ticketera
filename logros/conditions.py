from django.db.models import Q

from tickets.models import Order


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


CONDITION_CHECKERS = {
    'purchased_events': check_purchased_events,
}


def is_condition_met(user, achievement):
    checker = CONDITION_CHECKERS.get(achievement.condition_type)
    if not checker:
        return False
    return checker(user, achievement.condition_config or {})
