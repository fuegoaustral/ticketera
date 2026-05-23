from django.http import Http404

from events.models import Event


def user_is_event_admin(user, event):
    return event.admins.filter(id=user.id).exists()


def user_has_caja_access(user, event):
    return user_is_event_admin(user, event) or event.access_caja.filter(id=user.id).exists()


def get_event_for_admin(user, event_slug):
    try:
        event = Event.objects.get(slug=event_slug)
    except Event.DoesNotExist:
        raise Http404('Event not found')
    if not user_is_event_admin(user, event):
        from django.http import HttpResponseForbidden
        raise HttpResponseForbidden('No tenés permiso para administrar este evento')
    return event


def get_event_for_caja(user, event_slug):
    try:
        event = Event.objects.get(slug=event_slug)
    except Event.DoesNotExist:
        raise Http404('Event not found')
    if not user_has_caja_access(user, event):
        from django.http import HttpResponseForbidden
        raise HttpResponseForbidden('No tenés permiso para acceder a la caja de este evento')
    return event
