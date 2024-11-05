import hashlib
import hmac

from deprepagos import settings
from events.models import Event
from tickets.models import NewTicket, TicketType


def current_event(request):
    try:
        event = Event.objects.get(active=True)
    except Event.DoesNotExist:
        event = Event.objects.latest("id")

    context = {
        "event": event,
    }
    if request.user.is_authenticated:

        tickets = NewTicket.objects.filter(
            holder=request.user, event=event, owner=None
        ).all()

        tickets_dto = []

        for ticket in tickets:
            tickets_dto.append(ticket.get_dto(user=request.user))

        has_unassigned_tickets = any(
            ticket["is_owners"] is False for ticket in tickets_dto
        )
        has_transfer_pending = any(
            ticket["is_transfer_pending"] is True for ticket in tickets_dto
        )
        context.update(
            {
                "has_unassigned_tickets": has_unassigned_tickets,
                "has_transfer_pending": has_transfer_pending,
                "has_available_tickets": TicketType.objects.get_available_ticket_types_for_current_events().exists(),
                "holding_tickets": len(tickets)
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
