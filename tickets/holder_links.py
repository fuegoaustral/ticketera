"""Links directos a los bonos de un holder, para mails sin PDF.

En eventos con attendee_must_be_registered (Fuego Austral, Abracadabra) el bono
vale por la cuenta a la que está vinculado, así que en vez de adjuntar PDFs
mandamos links a Mis Bonos que llevan directo a cada bono y, si se puede, abren
el modal de transferencia.
"""
from urllib.parse import urlencode

from django.urls import reverse


def ticket_view_path(ticket, transfer=False):
    params = {'bono': str(ticket.key)}
    if transfer:
        params['transferir'] = '1'
    path = reverse('my_ticket_event', kwargs={'event_slug': ticket.event.slug})
    return f'{path}?{urlencode(params)}'


def holder_ticket_links(tickets, holder):
    """
    Un dict por bono con el path para verlo y, si el holder lo puede transferir
    (no es su bono propio, no está usado y el evento sigue en período de
    transferencias), el path que abre el modal de transferencia.
    """
    links = []
    for ticket in tickets:
        can_transfer = (
            ticket.event.transfer_period()
            and not ticket.is_used
            and ticket.owner_id != holder.id
        )
        links.append({
            'ticket': ticket,
            'short_key': str(ticket.key)[-8:],
            'is_mine': ticket.owner_id == holder.id,
            'view_path': ticket_view_path(ticket),
            'transfer_path': ticket_view_path(ticket, transfer=True) if can_transfer else None,
        })
    return links
