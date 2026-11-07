from decimal import Decimal, InvalidOperation

from django import template

from user_profile.display import display_full_name, display_label, display_name
from user_profile.phone import display_phone, whatsapp_number

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


# Aliases of the site-wide helpers in user_profile.display (also available as
# {% load display_name %}), kept so art templates keep loading art_format only.
@register.filter
def person_name(user):
    """Cómo mostrar a una persona: cómo le dicen, su nombre completo o, si no hay, su email."""
    return display_name(user)


@register.filter
def person_full_name(user):
    """El nombre completo, sólo cuando person_name muestra cómo le dicen."""
    return display_full_name(user)


@register.filter
def person_label(user):
    """Una línea para selects: «Juancho (Juan Bautista Sanchez)»."""
    return display_label(user)


@register.filter
def person_greeting(user):
    """Para saludar: cómo le dicen o su nombre completo; nunca el email."""
    return display_name(user, fallback_to_email=False)


@register.filter
def phone_display(phone):
    """Un teléfono legible: +54 9 11 5555-1234."""
    return display_phone(phone)


@register.filter
def phone_whatsapp(phone):
    """Los dígitos internacionales para abrir el chat de WhatsApp."""
    return whatsapp_number(phone)
