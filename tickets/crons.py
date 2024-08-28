from events.models import Event
from tickets.models import NewTicketTransfer, NewTicket


def ping(event, context):
    # Your cron job logic here
    print("Ping function executed.")

    current_event = Event.objects.get(active=True)

    tickets_with_no_owners = NewTicket.objects.filter(event=current_event, owner=None).all()

    pending_transfers = NewTicketTransfer.objects.filter(status='PENDING').select_related('ticket')
    pending_tickets = [transfer.ticket for transfer in pending_transfers]

    tickets_not_shared_yet = tickets_with_no_owners.exclude(id__in=[ticket.id for ticket in pending_tickets])

    print(pending_tickets)
    for ticket in pending_tickets:
        print(f"Pending ticket: {ticket}")

    print(pending_tickets)
    for ticket in tickets_with_no_owners:
        print(f"Ticket with no owner: {ticket}")

    print(tickets_not_shared_yet)
    for ticket in tickets_not_shared_yet:
        print(f"Ticket not shared yet: {ticket}")
