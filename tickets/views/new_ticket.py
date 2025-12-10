import json
import logging
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator
from django.db import transaction
from django.http import HttpResponseNotAllowed, HttpResponseForbidden, HttpResponse, HttpResponseBadRequest, \
    JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from tickets.models import NewTicket, NewTicketTransfer
from utils.email import send_mail

logger = logging.getLogger(__name__)


@login_required
def transfer_ticket(request, ticket_key):
    if request.method != 'POST':
        return JsonResponse({
            'status': 'ERROR',
            'message': 'Método no permitido'
        }, status=405)

    try:
        # Handle request.body which might be bytes
        body = request.body
        if isinstance(body, bytes):
            body = body.decode('utf-8')
        request_body = json.loads(body)
        logger.info(f"Transfer ticket request - ticket_key: {ticket_key}, email: {request_body.get('email')}")
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.error(f"Error parsing request body: {str(e)}, body type: {type(request.body)}")
        return JsonResponse({
            'status': 'ERROR',
            'message': 'Formato de solicitud inválido'
        }, status=400)

    if 'email' not in request_body:
        return JsonResponse({
            'status': 'ERROR',
            'message': 'Email requerido'
        }, status=400)

    email_param = request_body['email']
    email = email_param.lower()

    try:
        ticket = NewTicket.objects.get(key=ticket_key)
    except NewTicket.DoesNotExist:
        return JsonResponse({
            'status': 'ERROR',
            'message': 'Bono no encontrado'
        }, status=400)

    if ticket.holder != request.user:
        return JsonResponse({
            'status': 'ERROR',
            'message': 'No autorizado'
        }, status=403)

    if not ticket.event.transfer_period():
        return JsonResponse({
            'status': 'ERROR',
            'message': 'El período de transferencia ha finalizado'
        }, status=400)

    email_validator = EmailValidator()
    try:
        email_validator(email)
    except ValidationError as e:
        logger.warning(f"Invalid email format: {email}, error: {str(e)}")
        return JsonResponse({
            'status': 'ERROR',
            'message': 'Email inválido'
        }, status=400)

    destination_user = User.objects.filter(email=email).first()
    destination_user_exists = destination_user is not None and destination_user.profile.profile_completion == 'COMPLETE'
    logger.info(f"Destination user lookup - email: {email}, exists: {destination_user is not None}, profile_complete: {destination_user_exists}")

    pending_transfers = NewTicketTransfer.objects.filter(ticket=ticket, status='PENDING').exists()
    if pending_transfers:
        logger.warning(f"Pending transfer exists for ticket: {ticket_key}")
        return JsonResponse({
            'status': 'ERROR',
            'message': 'Ya existe una transferencia pendiente para este bono'
        }, status=400)

    if destination_user_exists is False:
        # Return error if email not found
        return JsonResponse({
            'status': 'ERROR',
            'error': 'EMAIL_NOT_FOUND',
            'message': 'El correo electrónico no fue encontrado. Por favor, verifica que el destinatario tenga una cuenta activa en Fuego Austral.'
        }, status=400)
    else:
        with transaction.atomic():
            destination_user = User.objects.get(email=email)
            destination_user_already_has_ticket = NewTicket.objects.filter(owner=destination_user).exists()

            new_ticket_transfer = NewTicketTransfer(
                ticket=ticket,
                tx_from=request.user,
                tx_to=destination_user,
                tx_to_email=destination_user.email,
                status='COMPLETED'
            )

            ticket.holder = destination_user
            if destination_user_already_has_ticket:
                ticket.owner = None
            else:
                ticket.owner = destination_user

            ticket.volunteer_ranger = None
            ticket.volunteer_transmutator = None
            ticket.volunteer_umpalumpa = None
            ticket.volunteer_mad = None

            new_ticket_transfer.save()
            ticket.save()

        send_mail(
            template_name='new_transfer_success',
            recipient_list=[email],
            context={
                'ticket_count': 1,
            }
        )

    return JsonResponse({'status': 'OK', 'destination_user_exists': destination_user_exists})


@login_required()
def cancel_ticket_transfer(request):
    if request.method != 'POST':
        return JsonResponse({
            'status': 'ERROR',
            'message': 'Método no permitido'
        }, status=405)

    try:
        # Handle request.body which might be bytes
        body = request.body
        if isinstance(body, bytes):
            body = body.decode('utf-8')
        request_body = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.error(f"Error parsing request body in cancel_ticket_transfer: {str(e)}")
        return JsonResponse({
            'status': 'ERROR',
            'message': 'Formato de solicitud inválido'
        }, status=400)

    if 'ticket_key' not in request_body:
        return JsonResponse({
            'status': 'ERROR',
            'message': 'Ticket key requerido'
        }, status=400)

    ticket_key = request_body['ticket_key']

    try:
        ticket = NewTicket.objects.get(key=ticket_key)
    except NewTicket.DoesNotExist:
        return JsonResponse({
            'status': 'ERROR',
            'message': 'Bono no encontrado'
        }, status=400)

    if ticket.holder != request.user:
        return JsonResponse({
            'status': 'ERROR',
            'message': 'No autorizado'
        }, status=403)

    if not ticket.event.transfer_period():
        return JsonResponse({
            'status': 'ERROR',
            'message': 'El período de transferencia ha finalizado'
        }, status=400)

    try:
        ticket_transfer = NewTicketTransfer.objects.get(ticket=ticket, status='PENDING', tx_from=request.user)
    except NewTicketTransfer.DoesNotExist:
        return JsonResponse({
            'status': 'ERROR',
            'message': 'Transferencia pendiente no encontrada'
        }, status=400)

    ticket_transfer.status = 'CANCELLED'
    ticket_transfer.save()

    return JsonResponse({'status': 'OK'})


@login_required()
def assign_ticket(request, ticket_key):
    if request.method != 'GET':
        return HttpResponseNotAllowed()

    ticket = NewTicket.objects.get(key=ticket_key)
    if ticket is None:
        return HttpResponseBadRequest('Ticket not found')

    if not (ticket.holder == request.user and ticket.owner == None):
        return HttpResponseForbidden('Not authorized')

    if ticket.is_used:
        return HttpResponseBadRequest('Cannot assign a used ticket')

    if not ticket.event.transfer_period():
        return HttpResponseBadRequest('Transfer period has ended')

    if NewTicket.objects.filter(holder=request.user, owner=request.user, event=ticket.event).exists():
        return HttpResponseBadRequest('User already has a ticket')

    ticket.owner = request.user
    # Clear volunteer fields when assigning ticket
    ticket.volunteer_ranger = None
    ticket.volunteer_transmutator = None
    ticket.volunteer_umpalumpa = None
    ticket.volunteer_mad = None
    ticket.save()

    # Redirect to the next URL if provided, otherwise to the event-specific page
    next_url = request.GET.get('next')
    if next_url:
        return redirect(next_url)
    else:
        return redirect(reverse('my_ticket_event', kwargs={'event_slug': ticket.event.slug}))


@login_required()
def unassign_ticket(request, ticket_key):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    ticket = NewTicket.objects.get(key=ticket_key)
    if ticket is None:
        return HttpResponseBadRequest()

    if not (ticket.holder == request.user and ticket.owner == request.user):
        return HttpResponseForbidden()

    if ticket.is_used:
        return HttpResponseBadRequest('Cannot unassign a used ticket')

    if not ticket.event.transfer_period():
        return HttpResponseBadRequest('')

    if ticket.event.transfers_enabled_until < timezone.now():
        return HttpResponseBadRequest('')

    ticket.volunteer_ranger = None
    ticket.volunteer_transmutator = None
    ticket.volunteer_umpalumpa = None
    ticket.owner = None

    ticket.save()

    # Redirect to the event-specific page instead of transferable_tickets
    return redirect(reverse('my_ticket_event', kwargs={'event_slug': ticket.event.slug}))
