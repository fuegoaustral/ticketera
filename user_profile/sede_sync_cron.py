import logging

from user_profile.services.sede_mercadopago import run_full_sync

logger = logging.getLogger(__name__)

try:
    from zappa.asynchronous import task
except Exception:  # pragma: no cover - zappa package may be unavailable locally
    task = None


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


if task:
    @task
    def sync_sede_members_async():
        """Run La Sede sync asynchronously via Zappa task."""
        return run_full_sync(log=logger)
else:
    def sync_sede_members_async():
        """Fallback when zappa.asynchronous is unavailable."""
        return run_full_sync(log=logger)


def dispatch_sede_members_sync():
    """
    Trigger La Sede sync from admin.
    On Zappa, enqueue async worker; locally fallback to direct execution.
    """
    if task:
        sync_sede_members_async()
        return {'queued': True, 'mode': 'zappa_task'}
    summary = sync_sede_members_async()
    return {'queued': False, 'mode': 'direct', 'summary': summary}
