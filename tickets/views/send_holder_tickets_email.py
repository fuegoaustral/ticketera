import hmac
import json
import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from events.models import Event
from tickets.holder_links import holder_ticket_links
from tickets.models import NewTicket
from tickets.ticket_pdf import build_new_ticket_pdf_bytes, new_ticket_pdf_filename
from utils.email import send_mail


def _authorization_matches_secret(request):
    secret = getattr(settings, 'SECRET', None)
    if secret is None or secret == '':
        return False
    header = request.headers.get('Authorization', '')
    return hmac.compare_digest(header, secret)


def _holder_tickets_for_event(event, payload):
    """
    Devuelve (queryset|None, error_tuple|None, lookup_mode).
    lookup_mode es 'user_id' o 'email'.
    """
    user_id = payload.get('user_id')
    if user_id is not None:
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return None, ('Invalid user_id', 400), None
        qs = (
            NewTicket.objects.filter(event=event, holder_id=uid)
            .exclude(holder__isnull=True)
            .select_related('event', 'ticket_type', 'owner', 'holder', 'owner__profile')
            .order_by('id')
        )
        return qs, None, 'user_id'

    email = (payload.get('email') or payload.get('holder_email') or '').strip()
    if not email:
        return None, ('Missing email or holder_email (or user_id) in JSON body', 400), None

    qs = (
        NewTicket.objects.filter(event=event, holder__email__iexact=email)
        .exclude(holder__isnull=True)
        .select_related('event', 'ticket_type', 'owner', 'holder', 'owner__profile')
        .order_by('id')
    )
    return qs, None, 'email'


def run_send_holder_tickets_email(event, payload=None, *, holder_user_id=None):
    """
    Envía al holder sus bonos del evento: PDFs adjuntos, o, si el evento exige
    cuenta por asistente (attendee_must_be_registered), links directos a cada
    bono con botón de transferir (sin PDF).

    - Uso HTTP / interno por JSON: run_send_holder_tickets_email(event, payload)
    - Uso broadcast (siempre por id de usuario): run_send_holder_tickets_email(event, holder_user_id=123)

    Devuelve dict con 'ok': bool y el resto de campos o 'error' + 'status'.
    """
    if holder_user_id is not None:
        try:
            uid = int(holder_user_id)
        except (TypeError, ValueError):
            return {'ok': False, 'error': 'Invalid holder_user_id', 'status': 400}
        tickets_qs = (
            NewTicket.objects.filter(event=event, holder_id=uid)
            .exclude(holder__isnull=True)
            .select_related('event', 'ticket_type', 'owner', 'holder', 'owner__profile')
            .order_by('id')
        )
        lookup_mode = 'user_id'
    else:
        tickets_qs, err, lookup_mode = _holder_tickets_for_event(event, payload or {})
        if err:
            msg, code = err
            return {'ok': False, 'error': msg, 'status': code}

    holder_ids = list(tickets_qs.values_list('holder_id', flat=True).distinct())
    if lookup_mode == 'email' and len(holder_ids) > 1:
        return {
            'ok': False,
            'error': 'Multiple accounts match this email; use user_id instead',
            'status': 400,
        }

    tickets = list(tickets_qs)
    if not tickets:
        return {'ok': False, 'error': 'No tickets for this person in this event', 'status': 404}

    holder = tickets[0].holder
    to_email = (holder.email or '').strip()
    if not to_email:
        return {'ok': False, 'error': 'Holder has no email', 'status': 400}

    n = len(tickets)

    if event.attendee_must_be_registered:
        # El bono vale por la cuenta, no por un PDF: mandamos links directos y botón de transferir.
        ticket_links = holder_ticket_links(tickets, holder)
        try:
            send_mail(
                template_name='holder_tickets',
                recipient_list=[to_email],
                context={
                    'event': event,
                    'ticket_links': ticket_links,
                    'has_transferable': any(link['transfer_path'] for link in ticket_links),
                },
            )
        except Exception as e:
            logging.exception('run_send_holder_tickets_email: send failed')
            return {'ok': False, 'error': f'Could not send email: {e}', 'status': 500}
        return _ok_result(event, holder, to_email, tickets)

    subject = f'Tus bonos — {event.name}' if n > 1 else f'Tu bono — {event.name}'

    if n > 1:
        intro = (
            f'Hola,\n\n'
            f'Adjuntamos los PDFs de tus {n} bonos para {event.name} '
            f'(un archivo por bono).\n\n'
            f'Tipos: {", ".join(t.ticket_type.name for t in tickets)}\n\n'
            f'Guardá los archivos y mostralos en el ingreso.\n'
        )
    else:
        t = tickets[0]
        intro = (
            f'Hola,\n\n'
            f'Adjuntamos el PDF de tu bono para {event.name}.\n'
            f'Tipo: {t.ticket_type.name}\n\n'
            f'Guardá el archivo y mostralo en el ingreso.\n'
        )

    msg = EmailMessage(
        subject=subject,
        body=intro,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_email],
    )

    try:
        for t in tickets:
            pdf_bytes = build_new_ticket_pdf_bytes(t)
            msg.attach(new_ticket_pdf_filename(t), pdf_bytes, 'application/pdf')
    except ImportError:
        return {'ok': False, 'error': 'PDF dependencies not installed', 'status': 500}
    except Exception as e:
        logging.exception('run_send_holder_tickets_email: PDF generation failed')
        return {'ok': False, 'error': f'PDF generation failed: {e}', 'status': 500}

    try:
        msg.send(fail_silently=False)
    except Exception as e:
        logging.exception('run_send_holder_tickets_email: send failed')
        return {'ok': False, 'error': f'Could not send email: {e}', 'status': 500}

    return _ok_result(event, holder, to_email, tickets)


def _ok_result(event, holder, to_email, tickets):
    return {
        'ok': True,
        'sent_to': to_email,
        'event_slug': event.slug,
        'ticket_count': len(tickets),
        'ticket_keys': [str(t.key) for t in tickets],
        'holder_id': holder.id,
        'status': 200,
    }


@require_POST
def send_holder_tickets_email(request, event_slug):
    """
    POST .../<event_slug>/send-holder-tickets-email/
    JSON: {"email": "..."} o {"holder_email": "..."} o {"user_id": <int>}
    Header Authorization == settings.SECRET.

    Envía al holder un correo con sus bonos de ese evento (ver run_send_holder_tickets_email).
    """
    if not _authorization_matches_secret(request):
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    event = Event.objects.filter(slug=event_slug).first()
    if not event:
        return JsonResponse({'error': 'Event not found'}, status=404)

    result = run_send_holder_tickets_email(event, payload)
    if not result['ok']:
        return JsonResponse({'error': result['error']}, status=result.get('status', 400))

    return JsonResponse(
        {
            'ok': True,
            'sent_to': result['sent_to'],
            'event_slug': event_slug,
            'ticket_count': result['ticket_count'],
            'ticket_keys': result['ticket_keys'],
        }
    )
