from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required

from events.models import Event
from tickets.forms import ProfileStep1Form, ProfileStep2Form
from tickets.models import NewTicket, NewTicketTransfer


@login_required
def my_tickets_view(request):
    event = Event.objects.get(active=True)
    tickets = NewTicket.objects.filter(holder=request.user, event=event)

    tickets_dto = []

    for ticket in tickets:
        transfer_pending = NewTicketTransfer.objects.filter(ticket=ticket, tx_from=request.user,
                                                            status='PENDING').first()
        tickets_dto.append({
            'key': ticket.key,
            'order': ticket.order.key,
            'ticket_type': ticket.ticket_type.name,
            'ticket_color': ticket.ticket_type.color,
            'emoji': ticket.ticket_type.emoji,
            'price': ticket.ticket_type.price,
            'is_transfer_pending': transfer_pending is not None,
            'transferring_to': transfer_pending.tx_to_email if transfer_pending else None,
            'is_owners': ticket.holder == ticket.owner,
            'volunteer_ranger': ticket.volunteer_ranger,
            'volunteer_transmutator': ticket.volunteer_transmutator,
            'volunteer_umpalumpa': ticket.volunteer_umpalumpa,
            'qr_code': ticket.generate_qr_code(),
        })
    tickets_dto = sorted(tickets_dto, key=lambda x: not x['is_owners'])

    # Check if any ticket is not owned by the current user
    has_unassigned_tickets = any(ticket['is_owners'] is False for ticket in tickets_dto)

    has_assigned_tickets = any(ticket['is_owners'] is True for ticket in tickets_dto)

    is_volunteer = any(ticket['is_owners'] is True and (ticket['volunteer_ranger'] or ticket['volunteer_transmutator'] or ticket['volunteer_umpalumpa']) for ticket in tickets_dto)

    # Check if any ticket has a transfer pending
    has_transfer_pending = any(ticket['is_transfer_pending'] is True for ticket in tickets_dto)

    return render(request, 'mi_fuego/mis_bonos/index.html', {
        'is_volunteer': is_volunteer,
        'has_assigned_tickets': has_assigned_tickets,
        'has_unassigned_tickets': has_unassigned_tickets,
        'has_transfer_pending': has_transfer_pending,
        'tickets_dto': tickets_dto,
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


def profile_congrats(request):
    return render(request, 'account/profile_congrats.html')


@login_required
def verification_congrats(request):
    return render(request, 'account/verification_congrats.html')
