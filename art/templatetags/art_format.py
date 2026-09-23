from decimal import Decimal, InvalidOperation

from django import template

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
