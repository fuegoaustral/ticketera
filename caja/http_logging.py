import json
import logging

import requests

logger = logging.getLogger('caja.http')

MAX_LOG_CHARS = 12000
REDACT_HEADER_KEYS = frozenset({'authorization', 'cookie', 'x-csrftoken'})
TRUNCATE_JSON_KEYS = frozenset({'qr_image'})


def _truncate(text, limit=MAX_LOG_CHARS):
    if text is None:
        return ''
    text = str(text)
    if len(text) <= limit:
        return text
    return f'{text[:limit]}… [truncated, {len(text)} chars total]'


def sanitize_headers(headers):
    if not headers:
        return {}
    return {
        key: '[REDACTED]' if str(key).lower() in REDACT_HEADER_KEYS else value
        for key, value in headers.items()
    }


def sanitize_json_value(key, value):
    if key in TRUNCATE_JSON_KEYS and isinstance(value, str):
        return f'[{key}: {len(value)} chars omitted]'
    if isinstance(value, dict):
        return {k: sanitize_json_value(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_json_value(key, item) for item in value]
    return value


def format_body_for_log(body):
    if body is None or body == '':
        return ''
    if isinstance(body, (dict, list)):
        payload = body
    elif isinstance(body, bytes):
        try:
            body = body.decode('utf-8')
        except UnicodeDecodeError:
            return f'[binary {len(body)} bytes]'
        try:
            payload = json.loads(body)
        except (TypeError, ValueError):
            return _truncate(body)
    elif isinstance(body, str):
        try:
            payload = json.loads(body)
        except (TypeError, ValueError):
            return _truncate(body)
    else:
        return _truncate(body)

    if isinstance(payload, (dict, list)):
        if isinstance(payload, dict):
            payload = {k: sanitize_json_value(k, v) for k, v in payload.items()}
        return _truncate(json.dumps(payload, ensure_ascii=False, default=str))
    return _truncate(payload)


def log_outgoing_http(*, service, method, url, headers=None, params=None, body=None):
    logger.info(
        '%s OUT >>> %s %s | headers=%s | params=%s | body=%s',
        service,
        method.upper(),
        url,
        sanitize_headers(headers),
        params or {},
        format_body_for_log(body),
    )


def log_incoming_http(*, service, method, url, status_code, body=None):
    logger.info(
        '%s IN <<< %s %s | status=%s | body=%s',
        service,
        method.upper(),
        url,
        status_code,
        format_body_for_log(body),
    )


def mp_request(method, url, **kwargs):
    headers = kwargs.get('headers')
    body = kwargs.get('json')
    if body is None and kwargs.get('data') is not None:
        body = kwargs.get('data')
    log_outgoing_http(
        service='mercadopago',
        method=method,
        url=url,
        headers=headers,
        params=kwargs.get('params'),
        body=body,
    )
    response = requests.request(method, url, **kwargs)
    log_incoming_http(
        service='mercadopago',
        method=method,
        url=url,
        status_code=response.status_code,
        body=response.text,
    )
    return response
