import logging
import uuid
from decimal import Decimal

from django.conf import settings

from caja.http_logging import mp_request

logger = logging.getLogger(__name__)

MP_ORDERS_URL = 'https://api.mercadopago.com/v1/orders'
MP_POS_URL = 'https://api.mercadopago.com/pos'
MP_TERMINALS_URL = 'https://api.mercadopago.com/terminals/v1/list'
MP_USERS_ME_URL = 'https://api.mercadopago.com/users/me'
MP_QR_MIN_AMOUNT = Decimal('15.00')

DEFAULT_STORE_LOCATION = {
    'street_number': '0123',
    'street_name': 'Av. Corrientes',
    'city_name': 'Palermo',
    'state_name': 'Capital Federal',
    'latitude': -34.588522,
    'longitude': -58.420391,
    'reference': 'Ticketera',
}

DEFAULT_BUSINESS_HOURS = {
    'monday': [{'open': '08:00', 'close': '22:00'}],
    'tuesday': [{'open': '08:00', 'close': '22:00'}],
    'wednesday': [{'open': '08:00', 'close': '22:00'}],
    'thursday': [{'open': '08:00', 'close': '22:00'}],
    'friday': [{'open': '08:00', 'close': '22:00'}],
    'saturday': [{'open': '10:00', 'close': '22:00'}],
    'sunday': [{'open': '10:00', 'close': '22:00'}],
}


class MercadoPagoInStoreError(Exception):
    def __init__(self, message, *, error_code=None, http_status=502):
        super().__init__(message)
        self.error_code = error_code
        self.http_status = http_status


def validate_mp_qr_amount(total_amount):
    amount = Decimal(total_amount)
    if amount < MP_QR_MIN_AMOUNT:
        raise MercadoPagoInStoreError(
            f'El monto mínimo para cobrar con MP QR es ${MP_QR_MIN_AMOUNT:.0f}',
            error_code='min_amount',
            http_status=400,
        )


def _parse_error_response(response):
    try:
        data = response.json()
    except ValueError:
        return response.text, None
    if isinstance(data, dict):
        errors = data.get('errors')
        if isinstance(errors, list) and errors:
            parts = []
            code = None
            for err in errors:
                if not isinstance(err, dict):
                    continue
                code = code or err.get('code')
                details = err.get('details')
                if isinstance(details, list):
                    parts.extend(str(item) for item in details if item)
                elif err.get('message'):
                    parts.append(str(err['message']))
            if parts:
                return '; '.join(parts), code
        causes = data.get('causes') or []
        descriptions = [
            cause.get('description', '')
            for cause in causes
            if isinstance(cause, dict) and cause.get('description')
        ]
        if descriptions:
            message = '; '.join(descriptions)
        else:
            message = data.get('message') or data.get('error') or response.text
        return message, data.get('error') or data.get('status')
    return response.text, None


def _headers(idempotency_key=None):
    headers = {
        'Authorization': f'Bearer {settings.MERCADOPAGO["ACCESS_TOKEN"]}',
        'Content-Type': 'application/json',
    }
    if idempotency_key:
        headers['X-Idempotency-Key'] = idempotency_key
    return headers


def _format_amount(amount):
    return f'{float(amount):.2f}'


def create_qr_order(*, external_reference, total_amount, external_pos_id, description='Venta caja'):
    validate_mp_qr_amount(total_amount)
    payload = {
        'type': 'qr',
        'total_amount': _format_amount(total_amount),
        'description': description[:150],
        'external_reference': external_reference[:64],
        'expiration_time': 'PT16M',
        'config': {
            'qr': {
                'external_pos_id': external_pos_id,
                'mode': 'dynamic',
            },
        },
        'transactions': {
            'payments': [{'amount': _format_amount(total_amount)}],
        },
    }
    response = mp_request(
        'POST',
        MP_ORDERS_URL,
        json=payload,
        headers=_headers(str(uuid.uuid4())),
        timeout=30,
    )
    if response.status_code >= 400:
        message, code = _parse_error_response(response)
        logger.error('MP QR order error: %s %s', response.status_code, message)
        http_status = response.status_code if 400 <= response.status_code < 500 else 502
        raise MercadoPagoInStoreError(message, error_code=code, http_status=http_status)
    return response.json()


def create_point_order(*, external_reference, total_amount, terminal_id, description='Venta caja'):
    payload = {
        'type': 'point',
        'external_reference': external_reference[:64],
        'expiration_time': 'PT16M',
        'description': description[:150],
        'transactions': {
            'payments': [{'amount': _format_amount(total_amount)}],
        },
        'config': {
            'point': {
                'terminal_id': terminal_id,
                'print_on_terminal': 'no_ticket',
            },
        },
    }
    response = mp_request(
        'POST',
        MP_ORDERS_URL,
        json=payload,
        headers=_headers(str(uuid.uuid4())),
        timeout=30,
    )
    if response.status_code >= 400:
        logger.error('MP Point order error: %s %s', response.status_code, response.text)
        raise MercadoPagoInStoreError(response.text)
    return response.json()


def get_order(mp_order_id):
    response = mp_request(
        'GET',
        f'{MP_ORDERS_URL}/{mp_order_id}',
        headers=_headers(),
        timeout=30,
    )
    if response.status_code >= 400:
        raise MercadoPagoInStoreError(response.text)
    return response.json()


def cancel_order(mp_order_id):
    response = mp_request(
        'POST',
        f'{MP_ORDERS_URL}/{mp_order_id}/cancel',
        headers=_headers(str(uuid.uuid4())),
        timeout=30,
    )
    if response.status_code >= 400:
        raise MercadoPagoInStoreError(response.text)
    return response.json()


def get_collector_user_id():
    configured = settings.MERCADOPAGO.get('COLLECTOR_USER_ID')
    if configured:
        return int(configured)
    response = mp_request('GET', MP_USERS_ME_URL, headers=_headers(), timeout=30)
    if response.status_code >= 400:
        message, code = _parse_error_response(response)
        raise MercadoPagoInStoreError(message, error_code=code)
    return int(response.json()['id'])


def search_store(user_id, external_id):
    response = mp_request(
        'GET',
        f'https://api.mercadopago.com/users/{user_id}/stores/search',
        params={'external_id': external_id},
        headers=_headers(),
        timeout=30,
    )
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        message, code = _parse_error_response(response)
        logger.error('MP search store error: %s %s', response.status_code, message)
        raise MercadoPagoInStoreError(message, error_code=code)
    data = response.json()
    if isinstance(data, list) and data:
        data = data[0]
    results = (data or {}).get('results') or []
    return results[0] if results else None


def create_store(*, user_id, name, external_id, location=None, business_hours=None):
    payload = {
        'name': name[:256],
        'external_id': external_id[:60],
        'location': location or DEFAULT_STORE_LOCATION,
        'business_hours': business_hours or DEFAULT_BUSINESS_HOURS,
    }
    response = mp_request(
        'POST',
        f'https://api.mercadopago.com/users/{user_id}/stores',
        json=payload,
        headers=_headers(str(uuid.uuid4())),
        timeout=30,
    )
    if response.status_code >= 400:
        message, code = _parse_error_response(response)
        logger.error('MP create store error: %s %s', response.status_code, message)
        raise MercadoPagoInStoreError(message, error_code=code)
    return response.json()


def create_pos(*, name, external_store_id, external_id, store_id):
    payload = {
        'name': name,
        'fixed_amount': False,
        'external_store_id': external_store_id,
        'external_id': external_id,
        'store_id': store_id,
        'category': 621102,
    }
    response = mp_request(
        'POST',
        MP_POS_URL,
        json=payload,
        headers=_headers(str(uuid.uuid4())),
        timeout=30,
    )
    if response.status_code >= 400:
        message, code = _parse_error_response(response)
        raise MercadoPagoInStoreError(message, error_code=code)
    return response.json()


def get_pos(pos_id):
    response = mp_request(
        'GET',
        f'{MP_POS_URL}/{pos_id}',
        headers=_headers(),
        timeout=30,
    )
    if response.status_code >= 400:
        message, code = _parse_error_response(response)
        raise MercadoPagoInStoreError(message, error_code=code)
    return response.json()


def pos_qr_display(pos_payload):
    """Return {type: 'image', url: ...} or {type: 'data', data: ...} for UI rendering."""
    if not pos_payload:
        return None
    qr = pos_payload.get('qr') or {}
    for url in (qr.get('image'), qr.get('template_image')):
        if url:
            return {'type': 'image', 'url': url}
    qr_code = pos_payload.get('qr_code')
    if qr_code:
        return {'type': 'data', 'data': qr_code}
    return None


def fetch_pos_qr_display(*, pos_id=None, external_id=None, store_id=None):
    if pos_id:
        return pos_qr_display(get_pos(pos_id))
    search = list_pos(external_id=external_id, store_id=store_id)
    results = search.get('results') or []
    if results:
        return pos_qr_display(results[0])
    return None


def list_pos(*, external_id=None, external_store_id=None, store_id=None, limit=50, offset=0):
    params = {'limit': limit, 'offset': offset}
    if external_id:
        params['external_id'] = external_id
    if external_store_id:
        params['external_store_id'] = external_store_id
    if store_id:
        params['store_id'] = store_id
    response = mp_request(
        'GET',
        MP_POS_URL,
        params=params,
        headers=_headers(),
        timeout=30,
    )
    if response.status_code >= 400:
        logger.error('MP list POS error: %s %s', response.status_code, response.text)
        raise MercadoPagoInStoreError(response.text)
    return response.json()


def list_terminals(store_id=None, pos_id=None, limit=50, offset=0):
    params = {'limit': limit, 'offset': offset}
    if store_id:
        params['store_id'] = store_id
    if pos_id:
        params['pos_id'] = pos_id
    response = mp_request(
        'GET',
        MP_TERMINALS_URL,
        params=params,
        headers=_headers(),
        timeout=30,
    )
    if response.status_code >= 400:
        logger.error('MP list terminals error: %s %s', response.status_code, response.text)
        raise MercadoPagoInStoreError(response.text)
    return response.json()


def iter_terminals(store_id=None, pos_id=None):
    """Yield all terminals from the account, paginating."""
    offset = 0
    limit = 50
    while True:
        data = list_terminals(store_id=store_id, pos_id=pos_id, limit=limit, offset=offset)
        terminals = (data.get('data') or {}).get('terminals') or []
        for terminal in terminals:
            yield terminal
        paging = data.get('paging') or {}
        total = paging.get('total', 0)
        offset += len(terminals)
        if not terminals or offset >= total:
            break


def is_order_paid(mp_order_data):
    status = mp_order_data.get('status', '')
    if status in ('processed', 'paid'):
        return True
    payments = mp_order_data.get('transactions', {}).get('payments', [])
    for payment in payments:
        if payment.get('status') in ('processed', 'approved', 'accredited'):
            return True
    return False


def is_order_terminal_failure(mp_order_data):
    status = mp_order_data.get('status', '')
    return status in ('canceled', 'cancelled', 'expMetro', 'expired')
