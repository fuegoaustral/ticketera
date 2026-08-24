import logging
from dataclasses import dataclass

from events.services.event_request_chatwoot import post_event_request_to_chatwoot
from events.services.event_request_slack import post_event_request_to_slack

logger = logging.getLogger(__name__)


@dataclass
class EventRequestNotifyResult:
    chatwoot: bool = False
    slack: bool = False

    @property
    def notified(self):
        return self.chatwoot or self.slack


def notify_event_request_for_review(event_request):
    """Notifica la propuesta a soporte por Chatwoot y Slack. Sin email."""
    result = EventRequestNotifyResult()
    try:
        result.chatwoot = bool(post_event_request_to_chatwoot(event_request))
    except Exception:
        logger.exception('Chatwoot falló para propuesta #%s', event_request.pk)
    try:
        result.slack = bool(post_event_request_to_slack(event_request))
    except Exception:
        logger.exception('Slack falló para propuesta #%s', event_request.pk)

    if not result.notified:
        logger.error('Propuesta #%s sin ningún canal de notificación', event_request.pk)
    return result
