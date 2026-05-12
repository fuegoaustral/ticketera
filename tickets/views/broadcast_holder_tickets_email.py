import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from events.models import Event
from tickets.models import NewTicket
from tickets.views.send_holder_tickets_email import (
    _authorization_matches_secret,
    run_send_holder_tickets_email,
)


BATCH_SIZE = 10


def _resolve_event_from_payload(payload):
    """
    Prioridad: event_id (pk), event_slug, event_name (exacto; único).
    Retorna (event, None) o (None, error_response_dict).
    """
    raw_id = payload.get('event_id')
    if raw_id is not None and raw_id != '':
        try:
            eid = int(raw_id)
        except (TypeError, ValueError):
            return None, {'error': 'Invalid event_id', 'status': 400}
        event = Event.objects.filter(pk=eid).first()
        if not event:
            return None, {'error': 'Event not found for this id', 'status': 404}
        return event, None

    event_slug = (payload.get('event_slug') or '').strip()
    event_name = (payload.get('event_name') or '').strip()

    if event_slug:
        event = Event.objects.filter(slug=event_slug).first()
        if not event:
            return None, {'error': 'Event not found for this slug', 'status': 404}
        return event, None

    if not event_name:
        return None, {
            'error': 'Missing event_id, event_slug, or event_name',
            'status': 400,
        }

    matches = list(Event.objects.filter(name=event_name))
    if not matches:
        return None, {'error': 'No event with this exact name', 'status': 404}
    if len(matches) > 1:
        return None, {
            'error': 'Multiple events share this name; pass event_id or event_slug to disambiguate',
            'event_slugs': [e.slug for e in matches if e.slug],
            'event_ids': [e.id for e in matches],
            'status': 400,
        }
    return matches[0], None


def _send_one_holder(event, holder_id):
    close_old_connections()
    try:
        return run_send_holder_tickets_email(event, holder_user_id=holder_id)
    finally:
        close_old_connections()


@require_POST
def broadcast_holder_tickets_email(request):
    """
    POST /broadcast-holder-ticket-emails/
    Header Authorization == settings.SECRET.
    JSON (uno): {"event_id": 123} | {"event_slug": "..."} | {"event_name": "..."}.

    Lista holders únicos con al menos un bono en el evento y, por lotes de 10
    en paralelo, ejecuta la misma lógica que send-holder-tickets-email (user_id).
    """
    if not _authorization_matches_secret(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    event, err = _resolve_event_from_payload(payload)
    if err:
        status = err.pop('status', 400)
        return JsonResponse(err, status=status)

    holder_ids = list(
        NewTicket.objects.filter(event=event, holder__isnull=False)
        .exclude(holder_id__isnull=True)
        .values_list('holder_id', flat=True)
        .distinct()
        .order_by('holder_id')
    )

    User = get_user_model()
    users_by_id = {u.pk: u for u in User.objects.filter(pk__in=holder_ids)} if holder_ids else {}
    holders_unique = [
        {
            'holder_id': hid,
            'email': ((users_by_id[hid].email or '') if hid in users_by_id else '').strip() or None,
        }
        for hid in holder_ids
    ]

    results = []
    for i in range(0, len(holder_ids), BATCH_SIZE):
        batch = holder_ids[i : i + BATCH_SIZE]
        with ThreadPoolExecutor(max_workers=BATCH_SIZE) as pool:
            future_map = {
                pool.submit(_send_one_holder, event, hid): hid for hid in batch
            }
            for fut in as_completed(future_map):
                hid = future_map[fut]
                try:
                    out = fut.result()
                except Exception as e:
                    logging.exception('broadcast_holder_tickets_email: worker failed holder_id=%s', hid)
                    results.append({'holder_id': hid, 'ok': False, 'error': str(e)})
                    continue
                row = {'holder_id': hid, 'ok': out['ok']}
                if out['ok']:
                    row['sent_to'] = out['sent_to']
                    row['ticket_count'] = out['ticket_count']
                else:
                    row['error'] = out['error']
                results.append(row)

    ok_n = sum(1 for r in results if r.get('ok'))
    fail_n = len(results) - ok_n

    return JsonResponse(
        {
            'ok': fail_n == 0,
            'event_id': event.id,
            'event_name': event.name,
            'event_slug': event.slug,
            'unique_holders': len(holder_ids),
            'holders': holders_unique,
            'processed': len(results),
            'succeeded': ok_n,
            'failed': fail_n,
            'batch_size': BATCH_SIZE,
            'results': sorted(results, key=lambda r: r['holder_id']),
        }
    )
