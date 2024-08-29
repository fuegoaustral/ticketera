import hashlib
import time
import json
import logging
from concurrent.futures import ThreadPoolExecutor, wait, ALL_COMPLETED
from django.db import connection
from events.models import Event
from tickets.models import MessageIdempotency

DASHES_LINE = '-' * 120


def send_pending_actions_emails(event, context):
    logging.info("Email cron job")
    logging.info("==============\n")

    current_event = Event.objects.get(active=True)
    send_pending_actions_emails_for_event(current_event)


def send_pending_actions_emails_for_event(current_event):
    pending_transfers_recipient = get_pending_transfers_recipients(current_event)
    pending_transfers_sender = get_pending_transfers_sender(current_event)
    unsent_tickets = get_unsent_tickets(current_event)

    total_unsent_tickets = sum(holder.pending_to_share_tickets for holder in unsent_tickets)

    log_pending_actions_summary(pending_transfers_recipient, total_unsent_tickets)

    start_time = time.perf_counter()

    with ThreadPoolExecutor() as executor:
        futures = create_futures(executor, pending_transfers_sender, pending_transfers_recipient, unsent_tickets,
                                 current_event)
        wait(futures, return_when=ALL_COMPLETED)

    log_execution_summary(futures, start_time)


def create_futures(executor, pending_transfers_sender, pending_transfers_recipient, unsent_tickets, current_event):
    sender_futures = [
        executor.submit(send_sender_pending_transfers_reminder, transfer, current_event)
        for transfer in pending_transfers_sender
    ]

    recipient_futures = [
        executor.submit(send_recipient_pending_transfers_reminder, transfer, current_event)
        for transfer in pending_transfers_recipient
    ]

    unsent_ticket_futures = [
        executor.submit(send_unsent_tickets_reminder_email, ticket, current_event)
        for ticket in unsent_tickets
    ]

    return sender_futures + recipient_futures + unsent_ticket_futures


def log_pending_actions_summary(pending_transfers_recipient, total_unsent_tickets):
    logging.info(DASHES_LINE)
    logging.info(f"| {len(pending_transfers_recipient)} Tickets waiting for recipient to create an account")
    logging.info(f"| {total_unsent_tickets} Tickets waiting for the holder to share")
    logging.info(DASHES_LINE)
    logging.info(
        f"{fibonacci_impares(5)} are the Fibonacci odd numbers < 30. We will send pending action reminders on days since the action MOD 30, that are on that sequence")
    logging.info(DASHES_LINE)


def log_execution_summary(futures, start_time):
    total_emails_sent = sum(future.result()[0] for future in futures)
    total_sms_sent = sum(future.result()[1] for future in futures)

    logging.info(DASHES_LINE)
    logging.info(
        f"Process time: {time.perf_counter() - start_time:.4f} seconds. Emails sent: {total_emails_sent}. SMS sent: {total_sms_sent}")
    logging.info(DASHES_LINE)


def send_recipient_pending_transfers_reminder(transfer, current_event):
    if should_send_reminder(transfer.max_days_ago):
        action = create_action_message(
            "sending a notification to the recipient",
            transfer.tx_to_email,
            transfer.max_days_ago,
            current_event.transfers_enabled_until
        )

        if not MessageIdempotency.objects.filter(email=transfer.tx_to_email, hash=hash_string(action)).exists():
            # TODO send email
            log_and_save_message(action, 'send_recipient_pending_transfers_reminder', transfer, current_event)
            return 1, 0

    return 0, 0


def send_sender_pending_transfers_reminder(transfer, current_event):
    emails_sent = send_email_reminder(transfer, current_event)
    messages_sent = send_sms_reminder(transfer, current_event)
    return emails_sent, messages_sent


def send_unsent_tickets_reminder_email(unsent_ticket, current_event):
    if should_send_reminder(unsent_ticket.max_days_ago):
        action = create_action_message(
            "sending a notification to the holder",
            unsent_ticket.email,
            unsent_ticket.max_days_ago,
            current_event.transfers_enabled_until,
            unsent_ticket.pending_to_share_tickets
        )

        if not MessageIdempotency.objects.filter(email=unsent_ticket.email, hash=hash_string(action)).exists():
            # TODO send email
            log_and_save_message(action, 'send_unsent_tickets_reminder_email', unsent_ticket, current_event)
            return 1, 0

    return 0, 0


def send_email_reminder(transfer, current_event):
    if should_send_reminder(transfer.max_days_ago):
        action = create_action_message(
            "sending a notification to the sender",
            transfer.tx_from_email,
            transfer.max_days_ago,
            current_event.transfers_enabled_until,
            transfer.tx_to_emails
        )

        if not MessageIdempotency.objects.filter(email=transfer.tx_from_email, hash=hash_string(action)).exists():
            # TODO send email
            log_and_save_message(action, 'send_sender_pending_transfers_reminder:email', transfer, current_event)
            return 1

    return 0


def send_sms_reminder(transfer, current_event):
    if transfer.max_days_ago == 2:
        action = create_action_message(
            "sending an SMS notification to the sender",
            transfer.tx_from_email,
            transfer.max_days_ago,
            current_event.transfers_enabled_until,
            transfer.tx_to_emails
        )

        if not MessageIdempotency.objects.filter(email=transfer.tx_from_email, hash=hash_string(action)).exists():
            # TODO send SMS
            log_and_save_message(action, 'send_sender_pending_transfers_reminder:sms', transfer, current_event)
            return 1

    return 0


def should_send_reminder(days_ago):
    return days_ago % 30 in fibonacci_impares(5)


def create_action_message(action_text, email, days_ago, transfers_enabled_until, additional_info=None):
    additional_info_text = f" You have {additional_info} pending tickets since {days_ago} days ago." if additional_info else ""
    return (
        f"{action_text} {email} to remember to create an account.{additional_info_text} "
        f"You have time until {transfers_enabled_until.strftime('%d/%m')}"
    )


def log_and_save_message(action, action_type, entity, current_event):
    logging.info(action)
    MessageIdempotency(
        email=entity.email,
        hash=hash_string(action),
        payload=json.dumps({
            'action': action_type,
            'entity': entity.to_dict(),
            'event_id': current_event.id
        })
    ).save()


class PendingTransferReceiver:
    def __init__(self, tx_to_email, max_days_ago):
        self.tx_to_email = tx_to_email
        self.max_days_ago = max_days_ago

    def to_dict(self):
        return {'tx_to_email': self.tx_to_email, 'max_days_ago': int(self.max_days_ago)}


class PendingTransferSender:
    def __init__(self, tx_from_email, tx_to_emails, max_days_ago):
        self.tx_from_email = tx_from_email
        self.tx_to_emails = tx_to_emails
        self.max_days_ago = max_days_ago

    def to_dict(self):
        return {'tx_from_email': self.tx_from_email, 'tx_to_emails': self.tx_to_emails,
                'max_days_ago': int(self.max_days_ago)}


class PendingTicketHolder:
    def __init__(self, email, pending_to_share_tickets, max_days_ago):
        self.email = email
        self.pending_to_share_tickets = pending_to_share_tickets
        self.max_days_ago = max_days_ago

    def to_dict(self):
        return {'email': self.email, 'pending_to_share_tickets': int(self.pending_to_share_tickets),
                'max_days_ago': int(self.max_days_ago)}


def get_pending_transfers_recipients(event):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT ntt.tx_to_email, MAX(EXTRACT(DAY FROM (NOW() - ntt.created_at))) as max_days_ago
            FROM tickets_newtickettransfer ntt
            INNER JOIN tickets_newticket nt ON nt.id = ntt.ticket_id
            WHERE ntt.status = 'PENDING' and nt.event_id=%s
            GROUP BY ntt.tx_to_email
        """, [event.id])
        return [PendingTransferReceiver(*row) for row in cursor.fetchall()]


def get_pending_transfers_sender(event):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT 
                u.email AS tx_from_email,
                STRING_AGG(DISTINCT email_count.tx_to_email_with_count, ', ') AS tx_to_emails,
                MAX(EXTRACT(DAY FROM (NOW() - ntt.created_at))) AS max_days_ago
            FROM tickets_newtickettransfer ntt
            INNER JOIN tickets_newticket nt ON nt.id = ntt.ticket_id
            INNER JOIN auth_user u ON u.id = ntt.tx_from_id
            INNER JOIN (
                SELECT
                    tx_to_email,
                    CONCAT(tx_to_email, ' (', COUNT(*)::text, ')') AS tx_to_email_with_count
                FROM tickets_newtickettransfer
                WHERE status = 'PENDING'
                GROUP BY tx_to_email
            ) AS email_count ON email_count.tx_to_email = ntt.tx_to_email
            WHERE ntt.status = 'PENDING' AND nt.event_id = %s
            GROUP BY u.email
        """, [event.id])

        return [PendingTransferSender(row[0], parse_emails(row[1]), row[2]) for row in cursor.fetchall()]


def parse_emails(tx_to_emails_raw):
    emails = []
    for email_with_count in tx_to_emails_raw.split(', '):
        email, count = email_with_count.rsplit(' (', 1)
        emails.append({'tx_to_email': email, 'pending_tickets': int(count.rstrip(')'))})
    return emails


def get_unsent_tickets(current_event):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT email,
                   COUNT(*) AS pending_to_share_tickets,
                   MAX(EXTRACT(DAY FROM (NOW() - tickets_newticket.created_at))) AS max_days_ago
            FROM tickets_newticket
            INNER JOIN auth_user ON auth_user.id = tickets_newticket.holder_id
            WHERE owner_id IS NULL AND event_id = %s
            GROUP BY email
        """, [current_event.id])

        return [PendingTicketHolder(*row) for row in cursor.fetchall()]


def fibonacci_impares(n, a=0, b=1, sequence=None):
    if sequence is None:
        sequence = []
    if len(sequence) >= n:
        return sequence
    if a % 2 != 0 and a not in sequence:
        sequence.append(a)
    return fibonacci_impares(n, b, a + b, sequence)


def hash_string(string_to_hash):
    return hashlib.sha256(string_to_hash.encode('utf-8')).hexdigest()
