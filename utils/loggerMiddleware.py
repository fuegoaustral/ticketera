import logging
import time
import uuid

from caja.http_logging import format_body_for_log, sanitize_headers

logger = logging.getLogger(__name__)


def _should_log_bodies(request):
    path = request.path
    if '/api/' in path:
        return True
    if 'cajas-v2' in path and request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        return True
    content_type = request.META.get('CONTENT_TYPE', '')
    return 'application/json' in content_type


class LoggerMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_uuid = uuid.uuid4()
        start_time = time.time()
        log_bodies = _should_log_bodies(request)

        logger.info('------------Request %s started------------', request_uuid)
        logger.info('Request method: %s', request.method)
        logger.info('Request path: %s', request.path)
        logger.info('Client IP: %s', request.META.get('REMOTE_ADDR'))
        if log_bodies:
            logger.info(
                'Request headers: %s',
                sanitize_headers({
                    key[5:].replace('_', '-').title(): value
                    for key, value in request.META.items()
                    if key.startswith('HTTP_')
                }),
            )
            if request.GET:
                logger.info('Request query: %s', dict(request.GET))
            if request.body:
                logger.info('Request body: %s', format_body_for_log(request.body))

        response = self.get_response(request)

        duration = time.time() - start_time
        logger.info('Request %s completed in %.2f seconds', request_uuid, duration)
        logger.info('Response status code: %s', response.status_code)
        response_body_size = len(response.content) if hasattr(response, 'content') else 0
        logger.info('Response body size: %s bytes', response_body_size)
        if log_bodies and hasattr(response, 'content') and response.content:
            logger.info('Response body: %s', format_body_for_log(response.content))
        logger.info('------------Request %s ended------------', request_uuid)

        return response
