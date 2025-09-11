import hashlib
import hmac

from deprepagos import settings
from events.models import Event
from tickets.models import NewTicket, TicketType, NewTicketTransfer


def current_event(request):
    # For the main page (/), always use main event
    if request.path == '/':
        try:
            event = Event.get_main_event()
        except Exception:
            event = Event.objects.latest("id")
    else:
        try:
            # Try to get event from request (URL slug, query params, session)
            from events.utils import get_event_from_request
            event = get_event_from_request(request)
        except Exception:
            # Fallback to main event or latest
            try:
                event = Event.get_main_event()
            except Exception:
                event = Event.objects.latest("id")

    # Check if there are multiple active events
    active_events = Event.get_active_events()
    has_multiple_events = active_events.count() > 1
    
    context = {
        "event": event,
        "has_multiple_events": has_multiple_events,
    }
    if request.user.is_authenticated:

        tickets = NewTicket.objects.filter(
            holder=request.user, event=event
        ).all()

        owns_ticket = NewTicket.objects.filter(
            holder=request.user, event=event, owner=request.user
        ).exists()

        tickets_dto = []

        for ticket in tickets:
            tickets_dto.append(ticket.get_dto(user=request.user))

        has_unassigned_tickets = any(
            ticket["is_owners"] is False for ticket in tickets_dto
        )
        has_transfer_pending = any(
            ticket["is_transfer_pending"] is True for ticket in tickets_dto
        )

        shared_tickets = NewTicketTransfer.objects.filter(
            tx_from=request.user, status="COMPLETED").count()

        # Count holding_tickets based on attendee_must_be_registered
        if event.attendee_must_be_registered:
            holding_tickets = NewTicket.objects.filter(
                holder=request.user, event=event, owner__isnull=True
            ).count()
        else:
            holding_tickets = len(tickets)


        # Count total tickets across all active events
        total_tickets = NewTicket.objects.filter(
            event__in=active_events, holder=request.user
        ).count()

        context.update(
            {
                "has_unassigned_tickets": has_unassigned_tickets,
                "has_transfer_pending": has_transfer_pending,
                "has_available_tickets": TicketType.objects.get_available_ticket_types_for_current_events().filter(event=event).exists(),
                "holding_tickets": holding_tickets,
                "shared_tickets": shared_tickets,
                "owns_ticket": owns_ticket,
                "total_tickets": total_tickets,
            }
        )
    return context


def app_url(request):
    return {"APP_URL": settings.APP_URL}


def donation_amount(request):
    return {"DONATION_AMOUNT": settings.DONATION_AMOUNT}


def chatwoot_token(request):
    return {"CHATWOOT_TOKEN": settings.CHATWOOT_TOKEN}


def env(request):
    return {"ENV": settings.ENV}


def chatwoot_identifier_hash(request):
    if hasattr(request, "user"):
        secret = bytes(settings.CHATWOOT_IDENTITY_VALIDATION, "utf-8")
        message = bytes(request.user.username, "utf-8")

        hash = hmac.new(secret, message, hashlib.sha256)
        identifier_hash = hash.hexdigest()
        return {"CHATWOOT_IDENTIFIER_HASH": identifier_hash}
    return {"CHATWOOT_IDENTIFIER_HASH": None}
