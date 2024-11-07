import logging
import time
import uuid

logger = logging.getLogger(__name__)


class LoggerMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        # One-time configuration and initialization.

    def __call__(self, request):
        # Generate a unique request UUID
        request_uuid = uuid.uuid4()
        start_time = time.time()

        # Log standard request information
        logger.info(f"------------Request {request_uuid} started------------")
        logger.info(f"Request method: {request.method}")
        logger.info(f"Request path: {request.path}")
        logger.info(f"Client IP: {request.META.get('REMOTE_ADDR')}")

        # Process the request and get the response
        response = self.get_response(request)

        # Calculate response time
        duration = time.time() - start_time
        logger.info(f"Request {request_uuid} completed in {duration:.2f} seconds")

        # Log response status code and body size
        logger.info(f"Response status code: {response.status_code}")
        response_body_size = len(response.content) if hasattr(response, 'content') else 0
        logger.info(f"Response body size: {response_body_size} bytes")
        logger.info(f"------------Request {request_uuid} ended------------")

        return response
