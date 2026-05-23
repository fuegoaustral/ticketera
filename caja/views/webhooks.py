import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from caja.models import CajaSale
from caja.mercadopago_instore import is_order_paid, is_order_terminal_failure
from caja.services.sales import finalize_caja_sale

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def mercadopago_instore_webhook(request):
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'status': 'invalid json'}, status=Cmd=400)

    logger.info('MP instore webhook: %s', payload)

    mp_order_id = payload.get('data', {}).get('id') or payload.get('id')
    external_ref = payload.get('external_reference', '')

    sale = None
    if external_ref.startswith('caja-sale-'):
        try:
            sale_id = int(external_ref.replace('caja-sale-', ''))
            sale = CajaSale.objects.filter(id=sale_id, status=CajaSale.Status.PENDING).first()
        except ValueError:
            pass
    if not sale and mp_order_id:
        sale = CajaSale.objects.filter(mp_order_id=mp_order_id, status=CajaSale.Status.PENDING).first()

    if not sale:
        return JsonResponse({'status': 'ignored'})

    if is_order_paid(payload):
        try:
            finalize_caja_sale(sale)
        except Exception as exc:
            logger.exception('Error finalizing caja sale %s: %s', sale.id, exc)
            return JsonResponse({'status': 'error'}, status=500)
    elif is_order_terminal_failure(payload):
        sale.status = (
            CajaSale.Status.CANCELLED
            if payload.get('status') in ('canceled', 'cancelled')
            else CajaSale.Status.EXPIRED
        )
        sale.processor_callback = payload
        sale.save(update_fields=['status', 'processor_callback', 'updated_at'])

    return JsonResponse({'status': 'ok'})
