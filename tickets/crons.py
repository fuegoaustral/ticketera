from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed, ALL_COMPLETED, wait

from django.utils import timezone

from events.models import Event
from tickets.models import NewTicketTransfer, NewTicket

DASHES_LINE = '-' * 120


def send_pending_actions_emails(event, context):
    print("Email cron job")
    print("==============")
    print("\n")
    # Your cron job logic here

    current_event = Event.objects.get(active=True)
    pending_transfers = get_pending_transfers()
    holder_summary, amount_tickets_not_shared_yet = get_unsent_tickets(current_event, pending_transfers)

    print(DASHES_LINE)
    print(f"| {len(pending_transfers)} Tickets waiting for recipient to create an account")
    print(f"| {amount_tickets_not_shared_yet} Tickets waiting for the holder to share")
    print(DASHES_LINE)

    with ThreadPoolExecutor() as executor:
        transfer_futures = [executor.submit(send_pending_transfers_reminder_email, transfer) for transfer in
                            pending_transfers]

        unsent_ticket_futures = [
            executor.submit(send_unsent_tickets_reminder_email, holder, summary, current_event)
            for holder, summary in holder_summary.items()
        ]

    wait(transfer_futures + unsent_ticket_futures, return_when=ALL_COMPLETED)


def send_pending_transfers_reminder_email(transfer):
    if (timezone.now() - transfer.created_at).days % 14 in [0, 1, 3, 8]:
        with ThreadPoolExecutor() as executor:
            wait(executor.submit(send_recipient_pending_transfers_reminder, transfer),
                 executor.submit(send_sende_pending_transfers_reminder, transfer),
                 return_when=ALL_COMPLETED)


def send_unsent_tickets_reminder_email(holder, summary, current_event):
    if summary['highest_days'] % 14 in [1, 3, 8]:
        print(
            f"sending a notification to the holder {holder} to remember to share the {summary['ticket_count']} tickets before {current_event.transfers_enabled_until}"
        )


def send_recipient_pending_transfers_reminder(transfer):
    print(f"sending a notification to the recipient {transfer.tx_to_email} to remember to create an account")


def send_sende_pending_transfers_reminder(transfer):
    print(
        f"sending a notification to the sender {transfer.tx_from.email} to remember to check with the recipient if they have created an account - Is the recipient's email correct?")


def get_pending_transfers():
    return NewTicketTransfer.objects.filter(status='PENDING').select_related('ticket').all()


def get_unsent_tickets(current_event, pending_transfers):
    tickets_not_shared_yet = NewTicket.objects.filter(event=current_event, owner=None).all().exclude(
        id__in=[ticket.id for ticket in [transfer.ticket for transfer in pending_transfers]])
    holder_summary = defaultdict(lambda: {'oldest_days': None, 'highest_days': 0, 'ticket_count': 0})

    for ticket in tickets_not_shared_yet:
        holder_email = ticket.holder.email
        days_ago = (timezone.now() - ticket.updated_at).days

        if holder_summary[holder_email]['oldest_days'] is None or days_ago > holder_summary[holder_email][
            'oldest_days']:
            holder_summary[holder_email]['oldest_days'] = days_ago

        if days_ago > holder_summary[holder_email]['highest_days']:
            holder_summary[holder_email]['highest_days'] = days_ago

        holder_summary[holder_email]['ticket_count'] += 1

    # Converting defaultdict to a regular dict for better readability
    return (dict(holder_summary), len(tickets_not_shared_yet))
