from django import template

from user_profile import display

register = template.Library()


@register.filter
def display_name(user):
    """{{ user|display_name }}: nickname, else full name, else email."""
    return display.display_name(user)


@register.filter
def greeting_name(user):
    """{{ user|greeting_name }}: like display_name but never the email ("Hola Juancho")."""
    return display.display_name(user, fallback_to_email=False)


@register.filter
def display_full_name(user):
    """{{ user|display_full_name }}: the full name, only when display_name shows the nickname."""
    return display.display_full_name(user)


@register.filter
def display_label(user):
    """{{ user|display_label }}: «Juancho (Juan Bautista Sanchez)»."""
    return display.display_label(user)
