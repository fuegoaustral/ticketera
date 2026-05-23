import logging

from user_profile.services.sede_mercadopago import run_full_sync

logger = logging.getLogger(__name__)


def sync_sede_members(event, context):
    """
    Scheduled task to sync La Sede members from MercadoPago subscriptions.
    Runs daily on prod via Zappa scheduled events.

    Uses MERCADOPAGO_ACCESS_TOKEN and DB credentials from the Lambda environment.
    """
    try:
        summary = run_full_sync(log=logger)
        if summary.get('error'):
            logger.error('La Sede sync failed: %s', summary['error'])
        return summary
    except Exception:
        logger.exception('Fatal error in La Sede membership sync')
        raise
