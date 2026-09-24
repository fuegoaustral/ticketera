from decimal import Decimal, InvalidOperation

from django import template
from django.core.exceptions import ObjectDoesNotExist

register = template.Library()


@register.filter
def art_money(value):
    """Format an art amount with Argentine separators and two decimals."""
    if value in (None, ''):
        return ''
    try:
        amount = Decimal(str(value)).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return value
    return f'{amount:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _nickname(user):
    try:
        return user.profile.nickname.strip()
    except ObjectDoesNotExist:
        return ''


@register.filter
def person_name(user):
    """Cómo mostrar a una persona: cómo le dicen, su nombre completo o, si no hay, su email."""
    if not user:
        return ''
    return _nickname(user) or user.get_full_name() or user.email


@register.filter
def person_full_name(user):
    """El nombre completo, sólo cuando person_name muestra cómo le dicen."""
    if not user or not _nickname(user):
        return ''
    return user.get_full_name()


@register.filter
def person_label(user):
    """Una línea para selects: «Juancho (Juan Bautista Sanchez)»."""
    full_name = person_full_name(user)
    return f'{person_name(user)} ({full_name})' if full_name else person_name(user)
