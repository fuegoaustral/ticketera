from django.shortcuts import render
from django.views.decorators.http import require_GET

from events.models import EventRequest
from events.services.event_request_actions import URL_ACTIONS, parse_action_token
from events.services.event_request_processing import approve_event_request, reject_event_request

REJECT_REASON = 'Rechazada desde Chatwoot'


@require_GET
def event_request_review_view(request, request_id, action):
    request_id = int(request_id)
    mapped = URL_ACTIONS.get(action)
    parsed = parse_action_token(request.GET.get('t', ''))
    if not mapped or not parsed or parsed[0] != mapped or parsed[1] != request_id:
        return render(request, 'events/event_request_review.html', {
            'ok': False,
            'title': 'Link inválido o vencido',
            'message': 'Este link de aprobación ya no es válido. Pedile a soporte que reenvíe la propuesta.',
        }, status=400)

    event_request = EventRequest.objects.filter(pk=request_id).first()
    if not event_request:
        return render(request, 'events/event_request_review.html', {
            'ok': False,
            'title': 'Propuesta no encontrada',
            'message': f'No existe la propuesta #{request_id}.',
        }, status=404)

    if mapped == 'approve':
        ok, reply = approve_event_request(event_request, actor_label='Chatwoot')
    else:
        ok, reply = reject_event_request(
            event_request,
            reason=REJECT_REASON,
            actor_label='Chatwoot',
        )

    event_request.refresh_from_db()
    already_done = not ok
    if mapped == 'approve':
        title = 'Propuesta aprobada' if ok or event_request.status == EventRequest.Status.APPROVED else 'No se pudo aprobar'
    else:
        title = 'Propuesta desaprobada' if ok or event_request.status == EventRequest.Status.REJECTED else 'No se pudo desaprobar'

    return render(request, 'events/event_request_review.html', {
        'ok': ok or already_done,
        'title': title,
        'message': reply,
        'event_request': event_request,
    })
