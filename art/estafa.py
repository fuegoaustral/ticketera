"""ESTAFA: equipo global que coordina Arte. Sólo actúa sobre eventos de Fuego Austral."""
from django.contrib.auth.models import User

from events.models import Event
from teams.models import TeamMembership

ESTAFA_SLUG = 'estafa'


def estafa_members():
    active = TeamMembership.objects.active().filter(team__slug=ESTAFA_SLUG)
    return User.objects.filter(pk__in=active.values('user_id'))


def is_estafa_member(user):
    if not user.is_authenticated:
        return False
    if not hasattr(user, '_is_estafa_member'):
        user._is_estafa_member = TeamMembership.objects.active().filter(
            team__slug=ESTAFA_SLUG, user=user,
        ).exists()
    return user._is_estafa_member


def can_access_estafa(user):
    return user.is_authenticated and (user.is_superuser or is_estafa_member(user))


def is_fuego_austral(event):
    # Los eventos de Fuego Austral son los que tienen voluntariado (Rangers, Transmutadores, CAOS).
    return event.has_volunteers


def estafa_events(user):
    events = Event.objects.filter(art_program__isnull=False).order_by('-start')
    if user.is_superuser:
        return events
    if is_estafa_member(user):
        return events.filter(has_volunteers=True)
    return events.none()


def can_coordinate(user, event):
    return user.is_authenticated and (
        user.is_superuser or (is_fuego_austral(event) and is_estafa_member(user))
    )
