"""How to show a person across the site.

Most places show how people like to be called (Profile.nickname). The legal
name (first_name + last_name) is only for formal places: bonos, the door,
documents, payments and access lists. Those keep using get_full_name().

Lists that render many people should select_related('profile') (or
'<fk>__profile') so these helpers don't query once per person.
"""
from django.core.exceptions import ObjectDoesNotExist


def nickname(user):
    """The user's nickname, or '' when unset or when the user has no Profile."""
    if not user or not getattr(user, 'is_authenticated', False):
        return ''
    try:
        profile = user.profile
    except (ObjectDoesNotExist, AttributeError):
        return ''
    return (profile.nickname or '').strip()


def full_name(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return ''
    return (user.get_full_name() or '').strip()


def display_name(user, fallback_to_email=True):
    """Nickname if set, else full name, else email (or '' with fallback_to_email=False)."""
    if not user or not getattr(user, 'is_authenticated', False):
        return ''
    name = nickname(user) or full_name(user)
    if name or not fallback_to_email:
        return name
    return user.email or user.username or ''


def display_full_name(user):
    """The full name, only when display_name shows the nickname instead (for a secondary line)."""
    if not nickname(user):
        return ''
    return full_name(user)


def display_label(user):
    """One line with both, for staff and pickers: «Juancho (Juan Bautista Sanchez)»."""
    name = display_name(user)
    extra = display_full_name(user)
    return f'{name} ({extra})' if extra else name
