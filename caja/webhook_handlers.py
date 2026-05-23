import logging

from caja.models import CajaSale
from caja.mercadopago_instore import (
    MercadoPagoInStoreError,
    get_order,
    is_order_paid,
    is_order_terminal_failure,
)
from caja.services.sales import finalize_caja_sale

logger = logging.getLogger(__name__)

CAJA_SALE_REF_PREFIX = 'caja-sale-'


def is_caja_external_reference(external_reference):
    return bool(external_reference and str(external_reference).startswith(CAJA_SALE_REF_PREFIX))


def _find_pending_caja_sale(*, external_reference='', mp_order_id=''):
    sale = None
    if is_caja_external_reference(external_reference):
        try:
            sale_id = int(str(external_reference).replace(CAJA_SALE_REF_PREFIX, ''))
            sale = CajaSale.objects.filter(id=sale_id, status=CajaSale.Status.PENDING).first()
        except ValueError:
            pass
    if not sale and mp_order_id:
        sale = CajaSale.objects.filter(
            mp_order_id=mp_order_id,
            status=CajaSale.Status.PENDING,
        ).first()
    return sale


def _apply_caja_mp_order_state(sale, mp_order_data, payment_callback=None):
    if is_order_paid(mp_order_data):
        payments = mp_order_data.get('transactions', {}).get('payments', [])
        net = None
        if payments:
            net = payments[0].get('paid_amount') or payments[0].get('amount')
        if payment_callback:
            sale.processor_callback = payment_callback
            sale.save(update_fields=['processor_callback', 'updated_at'])
        finalize_caja_sale(sale, net_received_amount=net)
        return 'paid'

    if is_order_terminal_failure(mp_order_data):
        sale.status = (
            CajaSale.Status.CANCELLED
            if mp_order_data.get('status') in ('canceled', 'cancelled')
            else CajaSale.Status.EXPIRED
        )
        sale.processor_callback = payment_callback or mp_order_data
        sale.save(update_fields=['status', 'processor_callback', 'updated_at'])
        return sale.status.lower()

    return 'pending'


def process_caja_mp_order(mp_order_data, payment_callback=None):
    """Finalize or cancel a pending CajaSale from MP order data. Returns outcome string."""
    external_ref = mp_order_data.get('external_reference', '')
    mp_order_id = mp_order_data.get('id', '')

    if not is_caja_external_reference(external_ref):
        return None

    sale = _find_pending_caja_sale(external_reference=external_ref, mp_order_id=mp_order_id)
    if not sale:
        logger.info('Caja MP webhook: no pending sale for ref=%s order=%s', external_ref, mp_order_id)
        return 'ignored'

    return _apply_caja_mp_order_state(sale, mp_order_data, payment_callback=payment_callback)


def handle_caja_payment_approved(payment):
    """Online-style payment.created webhook where external_reference is caja-sale-{id}."""
    external_ref = payment.get('external_reference', '')
    if not is_caja_external_reference(external_ref):
        return False

    sale = _find_pending_caja_sale(external_reference=external_ref)
    if not sale:
        logger.info('Caja payment webhook: no pending sale for ref=%s', external_ref)
        return True

    net = payment.get('transaction_details', {}).get('net_received_amount')
    if net is None:
        net = payment.get('transaction_amount')

    sale.processor_callback = payment
    sale.mp_payment_id = str(payment.get('id', '')) or sale.mp_payment_id
    sale.save(update_fields=['processor_callback', 'mp_payment_id', 'updated_at'])
    finalize_caja_sale(sale, net_received_amount=net)
    return True


def handle_caja_order_webhook(payload):
    """
    In-store order notifications (QR / Point) from the unified MP webhook.
    Returns True if the payload targeted a caja sale (handled or safely ignored).
    """
    action = payload.get('action', '')
    entity = payload.get('entity', '')
    if not (action.startswith('order.') or entity == 'order'):
        return False

    mp_order_id = payload.get('data', {}).get('id') or payload.get('id')
    if not mp_order_id:
        return False

    try:
        mp_order_data = get_order(mp_order_id)
    except MercadoPagoInStoreError as exc:
        logger.warning('Caja order webhook: could not fetch MP order %s: %s', mp_order_id, exc)
        return False

    if not is_caja_external_reference(mp_order_data.get('external_reference', '')):
        return False

    outcome = process_caja_mp_order(mp_order_data, payment_callback=mp_order_data)
    logger.info('Caja order webhook %s → %s', mp_order_id, outcome)
    return True
