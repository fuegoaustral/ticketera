"""
Event resolution utilities for multi-event support
"""
from django.http import Http404
from .models import Event


def get_event_from_request(request):
    """
    Resolve event from request using the following priority:
    1. URL slug parameter (for /<slug>/ URLs)
    2. Query parameter 'event' (slug)
    3. Query parameter 'event_id' (ID)
    4. Session stored event
    5. Main event (fallback)
    
    Returns Event instance or raises Http404 if not found
    """
    event = None
    
    # 1. Check URL slug (will be passed as kwarg in views)
    if hasattr(request, 'resolver_match') and request.resolver_match:
        slug = request.resolver_match.kwargs.get('event_slug')
        if slug:
            event = Event.get_by_slug(slug)
            if event:
                return event
    
    # 2. Check query parameter 'event' (slug)
    event_slug = request.GET.get('event')
    if event_slug:
        event = Event.get_by_slug(event_slug)
        if event:
            return event
    
    # 3. Check query parameter 'event_id' (ID)
    event_id = request.GET.get('event_id')
    if event_id:
        try:
            event = Event.objects.get(id=event_id, active=True)
            return event
        except Event.DoesNotExist:
            pass
    
    # 4. Check session stored event
    session_event_id = request.session.get('event_id')
    if session_event_id:
        try:
            event = Event.objects.get(id=session_event_id, active=True)
            return event
        except Event.DoesNotExist:
            pass
    
    # 5. Fallback to main event
    event = Event.get_main_event()
    if event:
        return event
    
    # 6. Last resort: get any active event
    event = Event.get_active_events().first()
    if event:
        return event
    
    raise Http404("No active events found")


def get_event_from_slug_or_main(slug=None):
    """
    Get event by slug or return main event if slug is None
    
    Args:
        slug: Event slug (optional)
        
    Returns:
        Event instance or None if not found
    """
    if slug:
        return Event.get_by_slug(slug)
    return Event.get_main_event()


def store_event_in_session(request, event):
    """
    Store event ID in session for checkout flow
    """
    request.session['event_id'] = event.id
    request.session['event_slug'] = event.slug


def get_event_from_session(request):
    """
    Get event from session
    """
    event_id = request.session.get('event_id')
    if event_id:
        try:
            return Event.objects.get(id=event_id, active=True)
        except Event.DoesNotExist:
            pass
    return None
