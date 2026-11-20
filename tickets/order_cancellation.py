"""Anulación de bonos de una orden desde el admin: refund en Mercado Pago, baja de bonos y mail al comprador."""
import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

import mercadopago
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from mercadopago.config import RequestOptions

from utils.email import send_mail
from .models import NewTicket, Order, OrderTicket

logger = logging.getLogger(__name__)

DEFAULT_REASON = 'se publicaron por error y no corresponden'


class OrderCancellationError(Exception):
    pass


@dataclass
class PaymentInfo:
    payment_id: str
    status: str
    amount: Decimal
    refunded: Decimal

    @property
    def refundable(self):
        return max(self.amount - self.refunded, Decimal('0'))


def _money(value):
    return Decimal(str(value or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def format_ars(value):
    return f'{_money(value):,.0f}'.replace(',', '.')


def payment_id_for(order):
    callback = order.processor_callback or {}
    payment_id = callback.get('id') if isinstance(callback, dict) else None
    return str(payment_id) if payment_id else None


def _sdk():
    return mercadopago.SDK(settings.MERCADOPAGO['ACCESS_TOKEN'])


def fetch_payment(order):
    """Estado actual del pago en MP (None si la orden no tiene pago de MP asociado)."""
    payment_id = payment_id_for(order)
    if not payment_id:
        return None
    result = _sdk().payment().get(payment_id)
    payment = result.get('response') or {}
    if result.get('status') != 200 or not payment.get('id'):
        raise OrderCancellationError(f'No se pudo consultar el pago {payment_id} en Mercado Pago: {payment}')
    return PaymentInfo(
        payment_id=payment_id,
        status=payment.get('status') or '',
        amount=_money(payment.get('transaction_amount')),
        refunded=_money(payment.get('transaction_amount_refunded')),
    )


def refund_payment(order, payment_id, amount, idempotency_key):
    options = RequestOptions(custom_headers={'X-Idempotency-Key': idempotency_key})
    result = _sdk().refund().create(payment_id, {'amount': float(amount)}, request_options=options)
    response = result.get('response') or {}
    if result.get('status') not in (200, 201) or response.get('status') not in ('approved', 'in_process'):
        raise OrderCancellationError(f'Mercado Pago rechazó el refund de ${amount}: {response}')
    logger.info('Order %s: refund MP %s por $%s (%s)', order.id, response.get('id'), amount, response.get('status'))
    return response


def cancellable_tickets(order):
    """Bonos de la orden que se pueden anular (no usados), los últimos emitidos primero."""
    return list(
        NewTicket.objects.filter(order=order, is_used=False)
        .select_related('ticket_type', 'holder', 'owner')
        .order_by('-id')
    )


def suggested_refund(order, tickets, quantity, payment):
    """Monto a devolver: si se anula todo lo que queda, lo que falta devolver del pago (incluye donaciones);
    si es parcial, el precio de los bonos anulados. Siempre acotado a lo que queda por devolver."""
    if payment is None:
        return Decimal('0')
    if quantity >= len(tickets):
        return payment.refundable
    amount = sum((_money(t.ticket_type.price) for t in tickets[:quantity]), Decimal('0'))
    return min(amount, payment.refundable)


def default_email_subject(order):
    event = order.event.name if order.event else 'Fuego Austral'
    return f'Anulación de tu bono para {event} (orden #{order.id})'


def default_email_body(order, quantity, refund_amount, ticket_type_names, reason=DEFAULT_REASON):
    event = order.event.name if order.event else 'Fuego Austral'
    bonos = 'los bonos' if quantity == 0 else ('el bono' if quantity == 1 else f'los {quantity} bonos')
    tipo = f' "{", ".join(ticket_type_names)}"' if ticket_type_names else ''
    lines = [
        f'Hola {order.first_name}:',
        '',
        f'Te escribimos porque {bonos}{tipo} de tu orden #{order.id} para {event} {reason}, '
        f'así que tuvimos que anular{"lo" if quantity == 1 else "los"}.',
        '',
    ]
    if refund_amount and refund_amount > 0:
        lines += [
            f'Ya te devolvimos ${format_ars(refund_amount)} por Mercado Pago, al mismo medio de pago que usaste. '
            'Según tu banco o tarjeta, puede tardar algunos días en verse reflejado.',
            '',
        ]
    lines += [
        'Te pedimos disculpas por las molestias. Si tenés cualquier duda, escribinos a bonos@fuegoaustral.org '
        'o por el chat de la web.',
        '',
        'Abrazo,',
        'Equipo de Bonos de Fuego Austral',
    ]
    return '\n'.join(lines)


@dataclass
class CancellationResult:
    cancelled_tickets: int
    refund_amount: Decimal
    refund_id: str | None
    order_refunded: bool
    email_sent: bool
    email_error: str | None = None


def cancel_order_tickets(order_id, *, quantity, refund_amount, do_refund, send_email,
                         email_subject, email_body, admin_user, idempotency_key):
    """Anula `quantity` bonos de la orden. Si hay que devolver plata, el refund en MP va primero
    y dentro de la transacción: si MP falla no se toca nada. El mail se manda después del commit."""
    refund_amount = _money(refund_amount)
    with transaction.atomic():
        order = Order.objects.select_for_update(of=('self',)).select_related('event').get(pk=order_id)
        if order.status != Order.OrderStatus.CONFIRMED:
            raise OrderCancellationError(f'La orden #{order.id} está {order.get_status_display()}, no se puede anular.')

        tickets = cancellable_tickets(order)
        if quantity < 0 or quantity > len(tickets):
            raise OrderCancellationError(f'La orden tiene {len(tickets)} bonos anulables, no se pueden anular {quantity}.')
        if quantity == 0 and tickets:
            raise OrderCancellationError('Elegí al menos un bono para anular.')

        refund = None
        if do_refund and refund_amount > 0:
            payment = fetch_payment(order)
            if payment is None:
                raise OrderCancellationError('La orden no tiene un pago de Mercado Pago para devolver.')
            if refund_amount > payment.refundable:
                raise OrderCancellationError(
                    f'Se quiere devolver ${refund_amount} pero en MP solo quedan ${payment.refundable} por devolver.')
            refund = refund_payment(order, payment.payment_id, refund_amount, idempotency_key)

        to_cancel = tickets[:quantity]
        per_type = {}
        for ticket in to_cancel:
            per_type[ticket.ticket_type_id] = per_type.get(ticket.ticket_type_id, 0) + 1
        NewTicket.objects.filter(pk__in=[t.pk for t in to_cancel]).delete()

        full = quantity == len(tickets)
        if full:
            order.status = Order.OrderStatus.REFUNDED
        else:
            # Anulación parcial: la orden sigue confirmada, así que bajamos lo comprado para no contar cupo de más.
            for ticket_type_id, count in per_type.items():
                line = OrderTicket.objects.filter(order=order, ticket_type_id=ticket_type_id).first()
                if line:
                    line.quantity = max(line.quantity - count, 0)
                    line.save(update_fields=['quantity', 'updated_at'])

        stamp = timezone.localtime().strftime('%d/%m/%Y %H:%M')
        note = f'[{stamp}] {admin_user.get_full_name() or admin_user.email} anuló {quantity} bono(s)'
        note += f' ({", ".join(str(t.key)[:8] for t in to_cancel)})' if to_cancel else ''
        if refund:
            note += f'; refund MP #{refund.get("id")} por ${refund_amount}'
        elif refund_amount > 0 and not do_refund:
            note += '; sin refund automático'
        if full:
            note += '; orden marcada como reembolsada'
        order.notes = f'{order.notes}\n{note}' if order.notes else note
        order.save(update_fields=['status', 'notes', 'updated_at'])

    result = CancellationResult(
        cancelled_tickets=quantity,
        refund_amount=refund_amount if refund else Decimal('0'),
        refund_id=str(refund.get('id')) if refund else None,
        order_refunded=full,
        email_sent=False,
    )
    if send_email:
        try:
            send_mail(
                template_name='order_cancelled',
                recipient_list=[order.email],
                context={'subject': email_subject, 'body': email_body, 'order': order},
            )
            result.email_sent = True
        except Exception as e:  # el refund y la baja ya están hechos; solo avisamos
            logger.exception('Order %s: falló el mail de anulación', order.id)
            result.email_error = str(e)
    return result
