import logging

from events.services.main_event import reconcile_main_event

logger = logging.getLogger(__name__)


def sync_main_event(event, context):
    """Cron: rota el evento principal si el actual ya finalizó."""
    main = reconcile_main_event()
    if main:
        logger.info('Evento principal vigente: #%s (%s)', main.pk, main.slug)
