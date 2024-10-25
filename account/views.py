from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import render, redirect
from django.urls import reverse

from events.models import Event
from tickets.models import NewTicket, NewTicketTransfer
from tickets.forms import ProfileStep1Form, ProfileStep2Form


@login_required
def my_fire_view(request):
    return redirect(reverse('my_tickets'))


@login_required
def my_tickets_view(request):
    event = Event.objects.get(active=True)
    my_ticket = NewTicket.objects.filter(holder=request.user, event=event, owner=request.user).first()
    tickets = NewTicket.objects.filter(holder=request.user, event=event, owner=None).all()

    tickets_dto = []

    for ticket in tickets:
        tickets_dto.append(ticket.get_dto(user=request.user))

    has_unassigned_tickets = any(ticket['is_owners'] is False for ticket in tickets_dto)
    has_transfer_pending = any(ticket['is_transfer_pending'] is True for ticket in tickets_dto)

    transferred_tickets = NewTicketTransfer.objects.filter(tx_from=request.user, status='COMPLETED').all()
    transferred_dto = []
    for transfer in transferred_tickets:
        transferred_dto.append({
            'tx_to_email': transfer.tx_to_email,
            'ticket_key': transfer.ticket.key,
            'ticket_type': transfer.ticket.ticket_type.name,
            'ticket_color': transfer.ticket.ticket_type.color,
            'emoji': transfer.ticket.ticket_type.emoji,
        })

    return render(request, 'mi_fuego/my_tickets/index.html', {
        'is_volunteer': my_ticket.is_volunteer() if my_ticket else False,
        'my_ticket': my_ticket.get_dto(user=request.user) if my_ticket else None,
        'has_unassigned_tickets': has_unassigned_tickets,
        'has_transfer_pending': has_transfer_pending,
        'tickets_dto': tickets_dto,
        'transferred_dto': transferred_dto,
        'event': event
    })


@login_required
def complete_profile(request):
    profile = request.user.profile
    error_message = None
    code_sent = False

    if profile.profile_completion == 'NONE':
        if request.method == 'POST':
            form = ProfileStep1Form(request.POST, instance=profile, user=request.user)
            if form.is_valid():
                form.save()
                profile.profile_completion = 'INITIAL_STEP'
                profile.save()
                return redirect('complete_profile')
        else:
            form = ProfileStep1Form(instance=profile, user=request.user)
        return render(request, 'account/complete_profile_step1.html', {'form': form})

    elif profile.profile_completion == 'INITIAL_STEP':
        form = ProfileStep2Form(request.POST or None, instance=profile)
        if request.method == 'POST':
            if 'send_code' in request.POST:
                if form.is_valid():
                    form.save()
                    form.send_verification_code()
                    code_sent = True
            elif 'verify_code' in request.POST:
                code_sent = True
                form = ProfileStep2Form(request.POST, instance=profile, code_sent=True)
                if form.is_valid():
                    if form.verify_code():
                        profile.profile_completion = 'COMPLETE'
                        profile.save()
                        return profile_congrats(request)
                    else:
                        error_message = "Código inválido. Por favor, intenta de nuevo."
            else:
                form = ProfileStep2Form(request.POST, instance=profile, code_sent=True)

        return render(request, 'account/complete_profile_step2.html', {
            'form': form,
            'error_message': error_message,
            'code_sent': code_sent,
            'profile': profile
        })
    else:
        return redirect('home')


@login_required
def profile_congrats(request):
    user = request.user
    pending_transfers = NewTicketTransfer.objects.filter(tx_to_email=user.email, status='PENDING').select_related(
        'ticket').all()

    if pending_transfers.exists():
        with transaction.atomic():
            user_already_has_ticket = NewTicket.objects.filter(owner=user).exists()
            for transfer in pending_transfers:
                transfer.status = 'COMPLETED'
                transfer.tx_to = user
                transfer.save()

                transfer.ticket.holder = user
                transfer.ticket.volunteer_ranger = None
                transfer.ticket.volunteer_transmutator = None
                transfer.ticket.volunteer_umpalumpa = None
                if user_already_has_ticket:
                    transfer.ticket.owner = None
                else:
                    transfer.ticket.owner = user
                    user_already_has_ticket = True

                transfer.ticket.save()

        return render(request, 'account/profile_congrats_with_tickets.html')
    else:
        return render(request, 'account/profile_congrats.html')


@login_required
def verification_congrats(request):
    return render(request, 'account/verification_congrats.html')