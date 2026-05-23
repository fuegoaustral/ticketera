import logging

from caja.mercadopago_instore import (
    MercadoPagoInStoreError,
    create_pos,
    create_store,
    fetch_pos_qr_display,
    get_collector_user_id,
    list_pos,
    search_store,
)
from caja.models import EventCajaMercadoPagoConfig

logger = logging.getLogger(__name__)


def default_mp_ids(event, caja):
    return f'EVT{event.id}', f'CAJA{caja.id}'


def _store_id_from_event_configs(event):
    return (
        EventCajaMercadoPagoConfig.objects.filter(
            event_caja__event=event,
            store_id__isnull=False,
        )
        .exclude(store_id=0)
        .values_list('store_id', flat=True)
        .first()
    )


def ensure_mp_store(event):
    """Return (store_id, external_store_id), creating the MP store if needed."""
    external_store_id = f'EVT{event.id}'

    cached_store_id = _store_id_from_event_configs(event)
    if cached_store_id:
        return int(cached_store_id), external_store_id

    user_id = get_collector_user_id()
    existing = search_store(user_id, external_store_id)
    if existing and existing.get('id'):
        store_id = int(existing['id'])
        _sync_event_store_id(event, store_id, external_store_id)
        return store_id, external_store_id

    try:
        store = create_store(
            user_id=user_id,
            name=event.name,
            external_id=external_store_id,
        )
    except MercadoPagoInStoreError:
        existing = search_store(user_id, external_store_id)
        if existing and existing.get('id'):
            store_id = int(existing['id'])
            _sync_event_store_id(event, store_id, external_store_id)
            return store_id, external_store_id
        raise
    store_id = int(store['id'])
    _sync_event_store_id(event, store_id, external_store_id)
    return store_id, external_store_id


def _sync_event_store_id(event, store_id, external_store_id):
    EventCajaMercadoPagoConfig.objects.filter(event_caja__event=event).update(
        store_id=store_id,
        external_store_id=external_store_id,
    )


def ensure_mp_qr_config(caja, event, *, force=False):
    """Ensure store + POS exist in MP for on-screen dynamic QR."""
    mp_config, _ = EventCajaMercadoPagoConfig.objects.get_or_create(event_caja=caja)
    if (
        not force
        and mp_config.qr_ready
        and mp_config.store_id
        and mp_config.pos_id
    ):
        return mp_config, False

    external_store_id, external_pos_id = default_mp_ids(event, caja)
    store_id, external_store_id = ensure_mp_store(event)

    mp_config.external_store_id = external_store_id
    mp_config.external_pos_id = external_pos_id
    mp_config.store_id = store_id

    try:
        search = list_pos(external_id=external_pos_id, store_id=store_id)
        results = search.get('results') or []
        if results:
            pos = results[0]
            mp_config.external_pos_id = pos.get('external_id') or external_pos_id
            mp_config.pos_id = pos.get('id')
            if pos.get('store_id'):
                mp_config.store_id = pos.get('store_id')
            mp_config.save()
            return mp_config, False
    except MercadoPagoInStoreError as exc:
        logger.warning('MP list POS failed for caja %s: %s', caja.id, exc)

    result = create_pos(
        name=caja.name,
        external_store_id=external_store_id,
        external_id=external_pos_id,
        store_id=store_id,
    )
    mp_config.pos_id = result.get('id')
    if result.get('store_id'):
        mp_config.store_id = result.get('store_id')
    mp_config.save()
    return mp_config, True


def get_caja_qr_display(mp_config):
    if not mp_config or not mp_config.qr_ready:
        return None
    try:
        if mp_config.pos_id:
            return fetch_pos_qr_display(pos_id=mp_config.pos_id)
        return fetch_pos_qr_display(
            external_id=mp_config.external_pos_id,
            store_id=mp_config.store_id,
        )
    except MercadoPagoInStoreError as exc:
        logger.warning('MP fetch POS QR failed for caja %s: %s', mp_config.event_caja_id, exc)
        return None


def terminal_linked_cajas(exclude_caja_id=None):
    qs = EventCajaMercadoPagoConfig.objects.exclude(terminal_id='').select_related(
        'event_caja', 'event_caja__event',
    )
    if exclude_caja_id:
        qs = qs.exclude(event_caja_id=exclude_caja_id)
    return {cfg.terminal_id: cfg.event_caja for cfg in qs}
