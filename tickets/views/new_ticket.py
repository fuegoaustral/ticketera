import json
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


@login_required
def transfer_ticket(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed('')

    request_body = json.loads(request.body)

    if 'email' not in request_body or 'ticket_key' not in request_body:
        return HttpResponseBadRequest('')

    email_param = request_body['email']
    email = email_param.lower()
    ticket_key = request_body['ticket_key']

    ticket = NewTicket.objects.get(key=ticket_key)
    if ticket is None:
        return HttpResponseBadRequest('')
    if ticket.holder != request.user:
        return HttpResponseForbidden('Qué hacés pedazo de gato? Quedaste re escrachado logi')
    if not ticket.event.transfer_period():
        return HttpResponseBadRequest('')

    email_validator = EmailValidator()
    try:
        email_validator(email)
    except ValidationError:
        return HttpResponseBadRequest('')


    destination_user = User.objects.filter(email=email).first()
    destination_user_exists = destination_user is not None and destination_user.profile.profile_completion == 'COMPLETE'


    pending_transfers = NewTicketTransfer.objects.filter(ticket=ticket, status='PENDING').exists()

    if pending_transfers:
        return HttpResponseBadRequest('')

    if destination_user_exists is False:
        new_ticket_transfer = NewTicketTransfer(
            ticket=ticket,
            tx_from=request.user,
            tx_to_email=email,
            status='PENDING'
        )
        new_ticket_transfer.save()
        # send email

        send_mail(
            template_name='new_transfer_no_account',
            recipient_list=[email],
            context={
                'ticket_count': 1,
                'destination_email': email,
                'sign_up_link': f"{reverse('account_signup')}?{urlencode({'email': email})}"
            }
        )
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
        return HttpResponseNotAllowed('')

    request_body = json.loads(request.body)

    if 'ticket_key' not in request_body:
        return HttpResponseBadRequest('')

    ticket_key = request_body['ticket_key']

    ticket = NewTicket.objects.get(key=ticket_key)

    if ticket is None:
        return HttpResponseBadRequest('')

    if ticket.holder != request.user:
        return HttpResponseForbidden('Qué hacés pedazo de gato? Quedaste re escrachado logi')

    if not ticket.event.transfer_period():
        return HttpResponseBadRequest('')

    ticket_transfer = NewTicketTransfer.objects.get(ticket=ticket, status='PENDING', tx_from=request.user)

    if ticket_transfer is None:
        return HttpResponseBadRequest('')

    ticket_transfer.status = 'CANCELLED'
    ticket_transfer.save()

    return HttpResponse('OK')


@login_required()
def assign_ticket(request, ticket_key):
    if request.method != 'GET':
        return HttpResponseNotAllowed()

    ticket = NewTicket.objects.get(key=ticket_key)
    if ticket is None:
        return HttpResponseBadRequest()

    if not (ticket.holder == request.user and ticket.owner == None):
        return HttpResponseForbidden()

    if not ticket.event.transfer_period():
        return HttpResponseBadRequest('')

    if NewTicket.objects.filter(holder=request.user, owner=request.user).exists():
        return HttpResponseBadRequest

    ticket.owner = request.user
    ticket.save()

    return redirect(reverse('my_ticket'))


@login_required()
def unassign_ticket(request, ticket_key):
    if request.method != 'GET':
        return HttpResponseNotAllowed()

    ticket = NewTicket.objects.get(key=ticket_key)
    if ticket is None:
        return HttpResponseBadRequest()

    if not (ticket.holder == request.user and ticket.owner == request.user):
        return HttpResponseForbidden()

    if not ticket.event.transfer_period():
        return HttpResponseBadRequest('')

    if ticket.event.transfers_enabled_until < timezone.now():
        return HttpResponseBadRequest('')

    ticket.volunteer_ranger = None
    ticket.volunteer_transmutator = None
    ticket.volunteer_umpalumpa = None
    ticket.owner = None

    ticket.save()

    return redirect(reverse('transferable_tickets'))
