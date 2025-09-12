from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpResponseNotAllowed, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from events.utils import get_event_from_request
from tickets.models import NewTicket, NewTicketTransfer, Order
from .forms import ProfileStep1Form, ProfileStep2Form, VolunteeringForm


@login_required
def my_fire_view(request):
    return redirect(reverse("my_ticket"))


def show_past_events(request):
    """Show past events tickets"""
    from django.utils import timezone
    
    # Get past events (include both active and inactive events that have ended)
    past_events = Event.objects.filter(
        end__lt=timezone.now()
    ).order_by('-end')
    
    # Get the main event for context
    main_event = Event.get_main_event()
    
    # Get tickets from past events
    past_tickets = NewTicket.objects.filter(
        holder=request.user, 
        event__in=past_events
    ).order_by("-event__end", "event__name", "owner").all()
    
    # Get the first ticket for the main event (for backward compatibility)
    my_ticket = NewTicket.objects.filter(
        holder=request.user, event=main_event, owner=request.user
    ).first() if main_event else None

    # Organize past tickets by event
    past_tickets_by_event = {}
    for ticket in past_tickets:
        event_key = ticket.event.slug or ticket.event.id
        if event_key not in past_tickets_by_event:
            past_tickets_by_event[event_key] = {
                'event': {
                    'id': ticket.event.id,
                    'name': ticket.event.name,
                    'slug': ticket.event.slug,
                    'start': ticket.event.start,
                    'end': ticket.event.end,
                    'location': ticket.event.location,
                    'location_url': ticket.event.location_url,
                },
                'tickets': []
            }
        
        ticket_dto = ticket.get_dto(user=request.user)
        # Add tag to distinguish between Mine and Guest tickets
        ticket_dto['tag'] = 'Mine' if ticket.owner == request.user else 'Guest'
        # Add event information for this specific ticket
        ticket_dto['event'] = {
            'name': ticket.event.name,
            'slug': ticket.event.slug,
            'start': ticket.event.start,
            'end': ticket.event.end,
        }
        # Add user information for Mine tickets
        if ticket.owner == request.user:
            ticket_dto['user_info'] = {
                'first_name': request.user.first_name,
                'last_name': request.user.last_name,
                'dni': request.user.profile.document_number
            }
        
        past_tickets_by_event[event_key]['tickets'].append(ticket_dto)

    # Get events where user has tickets, prioritizing main event
    user_events = Event.get_active_events().filter(
        newticket__holder=request.user
    ).distinct().order_by('-is_main', 'name')
    
    return render(
        request,
        "mi_fuego/my_tickets/past_events.html",
        {
            "is_volunteer": my_ticket.is_volunteer() if my_ticket else False,
            "my_ticket": my_ticket.get_dto(user=request.user) if my_ticket else None,
            "event": main_event,  # Use main event for context
            "active_events": user_events,  # Only events where user has tickets
            "nav_primary": "tickets",
            "nav_secondary": "past_events",
            'now': timezone.now(),
            'past_tickets_by_event': past_tickets_by_event,  # Past events tickets
            'attendee_must_be_registered': main_event.attendee_must_be_registered if main_event else False,
        },
    )


@login_required
def my_ticket_view(request, event_slug=None):
    from django.utils import timezone
    
    # If event_slug is provided, show tickets for that specific event
    if event_slug:
        # Special case: if slug is "eventos-anteriores", show past events
        if event_slug == "eventos-anteriores":
            return show_past_events(request)
        try:
            current_event = Event.get_by_slug(event_slug)
            # Check if event was found
            if not current_event:
                # If event doesn't exist, redirect to main event
                return redirect('my_ticket')
                
            # Get tickets for this specific event
            all_tickets = NewTicket.objects.filter(
                holder=request.user, 
                event=current_event
            ).order_by("owner").all()
            
            # Also get tickets that were transferred from this user (completed transfers)
            from tickets.models import NewTicketTransfer
            transferred_tickets = NewTicketTransfer.objects.filter(
                tx_from=request.user,
                status='COMPLETED',
                ticket__event=current_event
            ).select_related('ticket').all()
            
            # Add transferred tickets to the list
            for transfer in transferred_tickets:
                if transfer.ticket not in all_tickets:
                    all_tickets = list(all_tickets) + [transfer.ticket]
            
            # Get the first ticket for this event (for backward compatibility)
            my_ticket = NewTicket.objects.filter(
                holder=request.user, event=current_event, owner=request.user
            ).first()
            
            # Organize tickets for this event
            tickets_dto = []
            all_unassigned = True
            for ticket in all_tickets:
                ticket_dto = ticket.get_dto(user=request.user)
                # Add tag to distinguish between Mine and Guest tickets
                ticket_dto['tag'] = 'Mine' if ticket.owner == request.user else 'Guest'
                # Add event information for this specific ticket
                ticket_dto['event'] = {
                    'name': ticket.event.name,
                    'slug': ticket.event.slug,
                    'start': ticket.event.start,
                    'end': ticket.event.end,
                }
                # Add user information for Mine tickets
                if ticket.owner == request.user:
                    ticket_dto['user_info'] = {
                        'first_name': request.user.first_name,
                        'last_name': request.user.last_name,
                        'dni': request.user.profile.document_number
                    }
                    all_unassigned = False
                else:
                    # For Guest tickets, add transfer information similar to transferable_tickets
                    from tickets.models import NewTicketTransfer
                    transfer_pending = NewTicketTransfer.objects.filter(
                        ticket=ticket, 
                        tx_from=request.user, 
                        status='PENDING'
                    ).first()
                    transfer_completed = NewTicketTransfer.objects.filter(
                        ticket=ticket, 
                        tx_from=request.user, 
                        status='COMPLETED'
                    ).first()
                    
                    if transfer_pending:
                        ticket_dto['is_transfer_pending'] = True
                        ticket_dto['transferring_to'] = transfer_pending.tx_to_email
                        ticket_dto['is_transfer_completed'] = False
                        ticket_dto['transferred_to'] = None
                    elif transfer_completed:
                        ticket_dto['is_transfer_pending'] = False
                        ticket_dto['transferring_to'] = None
                        ticket_dto['is_transfer_completed'] = True
                        ticket_dto['transferred_to'] = transfer_completed.tx_to_email
                    else:
                        ticket_dto['is_transfer_pending'] = False
                        ticket_dto['transferring_to'] = None
                        ticket_dto['is_transfer_completed'] = False
                        ticket_dto['transferred_to'] = None
                tickets_dto.append(ticket_dto)
            
            # Count unshared tickets (Guest tickets that are not transferred and not pending)
            unshared_tickets_count = 0
            for ticket_dto in tickets_dto:
                if (ticket_dto['tag'] == 'Guest' and 
                    not ticket_dto.get('is_transfer_pending', False) and 
                    not ticket_dto.get('is_transfer_completed', False)):
                    unshared_tickets_count += 1
            
            # Get events where user has tickets, prioritizing main event
            user_events = Event.get_active_events().filter(
                newticket__holder=request.user
            ).distinct().order_by('-is_main', 'name')
            
            return render(
                request,
                "mi_fuego/my_tickets/my_ticket.html",
                {
                    "is_volunteer": my_ticket.is_volunteer() if my_ticket else False,
                    "my_ticket": my_ticket.get_dto(user=request.user) if my_ticket else None,
                    "event": current_event,  # Use current event for context
                    "active_events": user_events,  # Only events where user has tickets
                    "nav_primary": "tickets",
                    "nav_secondary": "my_ticket",
                    'now': timezone.now(),
                    'tickets_dto': tickets_dto,  # Simple list for single event
                    'attendee_must_be_registered': current_event.attendee_must_be_registered,
                    'all_unassigned': all_unassigned and not current_event.attendee_must_be_registered,
                    'unshared_tickets_count': unshared_tickets_count,
                },
            )
        except Event.DoesNotExist:
            # If event doesn't exist, redirect to main event
            return redirect('my_ticket')
    
    # If no event_slug, determine what to show by default
    # Priority: 1) Main event if user has tickets, 2) Any active event if user has tickets, 3) Past events
    
    # Get events where user has tickets, prioritizing main event
    user_events = Event.get_active_events().filter(
        newticket__holder=request.user
    ).distinct().order_by('-is_main', 'name')
    
    # If user has tickets in active events, redirect to the first one (main event priority)
    if user_events.exists():
        main_event_with_tickets = user_events.first()
        return redirect('my_ticket_event', event_slug=main_event_with_tickets.slug)
    
    # If no active events with tickets, show past events
    past_events = Event.get_active_events().filter(
        end__lt=timezone.now()
    ).order_by('-end')
    
    # Get the main event for context
    main_event = Event.get_main_event()
    
    # Get tickets from past events
    past_tickets = NewTicket.objects.filter(
        holder=request.user, 
        event__in=past_events
    ).order_by("-event__end", "event__name", "owner").all()
    
    # Get the first ticket for the main event (for backward compatibility)
    my_ticket = NewTicket.objects.filter(
        holder=request.user, event=main_event, owner=request.user
    ).first() if main_event else None

    # Organize past tickets by event
    past_tickets_by_event = {}
    for ticket in past_tickets:
        event_key = ticket.event.slug or ticket.event.id
        if event_key not in past_tickets_by_event:
            past_tickets_by_event[event_key] = {
                'event': {
                    'id': ticket.event.id,
                    'name': ticket.event.name,
                    'slug': ticket.event.slug,
                    'start': ticket.event.start,
                    'end': ticket.event.end,
                    'location': ticket.event.location,
                    'location_url': ticket.event.location_url,
                },
                'tickets': []
            }
        
        ticket_dto = ticket.get_dto(user=request.user)
        # Add tag to distinguish between Mine and Guest tickets
        ticket_dto['tag'] = 'Mine' if ticket.owner == request.user else 'Guest'
        # Add event information for this specific ticket
        ticket_dto['event'] = {
            'name': ticket.event.name,
            'slug': ticket.event.slug,
            'start': ticket.event.start,
            'end': ticket.event.end,
        }
        # Add user information for Mine tickets
        if ticket.owner == request.user:
            ticket_dto['user_info'] = {
                'first_name': request.user.first_name,
                'last_name': request.user.last_name,
                'dni': request.user.profile.document_number
            }
        
        past_tickets_by_event[event_key]['tickets'].append(ticket_dto)

    # Get events where user has tickets, prioritizing main event
    user_events = Event.get_active_events().filter(
        newticket__holder=request.user
    ).distinct().order_by('-is_main', 'name')
    
    return render(
        request,
        "mi_fuego/my_tickets/past_events.html",
        {
            "is_volunteer": my_ticket.is_volunteer() if my_ticket else False,
            "my_ticket": my_ticket.get_dto(user=request.user) if my_ticket else None,
            "event": main_event,  # Use main event for context
            "active_events": user_events,  # Only events where user has tickets
            "nav_primary": "tickets",
            "nav_secondary": "past_events",
            'now': timezone.now(),
            'past_tickets_by_event': past_tickets_by_event,  # Past events tickets
            'attendee_must_be_registered': main_event.attendee_must_be_registered if main_event else False,
        },
    )


@login_required
def transferable_tickets_view(request, event_slug=None):
    # Get the specific event from slug
    if event_slug:
        current_event = Event.get_by_slug(event_slug)
        if not current_event:
            raise Http404("Event not found")
    else:
        current_event = Event.get_main_event()
    
    # If attendees don't need to be registered, redirect to my_ticket view
    if not (current_event and current_event.attendee_must_be_registered):
        return redirect(reverse("my_ticket_event", kwargs={"event_slug": current_event.slug}))

    # Get tickets for the specific event only
    tickets = (
        NewTicket.objects.filter(holder=request.user, event=current_event)
        .exclude(owner=request.user)
        .order_by("owner")
        .all()
    )
    my_ticket = NewTicket.objects.filter(
        holder=request.user, event=current_event, owner=request.user
    ).first()

    tickets_dto = []

    for ticket in tickets:
        ticket_dto = ticket.get_dto(user=request.user)
        # Add event information for this specific ticket
        ticket_dto['event'] = {
            'name': ticket.event.name,
            'slug': ticket.event.slug,
            'start': ticket.event.start,
            'end': ticket.event.end,
        }
        tickets_dto.append(ticket_dto)

    transferred_tickets = NewTicketTransfer.objects.filter(
        tx_from=request.user, status="COMPLETED", ticket__event=current_event
    ).all()
    transferred_dto = []
    for transfer in transferred_tickets:
        transferred_dto.append(
            {
                "tx_to_email": transfer.tx_to_email,
                "ticket_key": transfer.ticket.key,
                "ticket_type": transfer.ticket.ticket_type.name,
                "ticket_color": transfer.ticket.ticket_type.color,
                "emoji": transfer.ticket.ticket_type.emoji,
                "event": {
                    'name': transfer.ticket.event.name,
                    'slug': transfer.ticket.event.slug,
                    'start': transfer.ticket.event.start,
                    'end': transfer.ticket.event.end,
                }
            }
        )

    # Get events where user has tickets, prioritizing main event
    user_events = Event.get_active_events().filter(
        newticket__holder=request.user
    ).distinct().order_by('-is_main', 'name')
    
    return render(
        request,
        "mi_fuego/my_tickets/transferable_tickets.html",
        {
            "my_ticket": my_ticket,
            "tickets_dto": tickets_dto,
            "transferred_dto": transferred_dto,
            "event": current_event,  # Use current event for context
            "active_events": user_events,  # Only events where user has tickets
            "nav_primary": "tickets",
            "nav_secondary": "transferable_tickets",
        },
    )


@login_required
def complete_profile(request):
    profile = request.user.profile
    error_message = None
    code_sent = False

    if profile.profile_completion == "NONE":
        if request.method == "POST":
            form = ProfileStep1Form(request.POST, instance=profile, user=request.user)
            if form.is_valid():
                form.save()
                profile.profile_completion = "INITIAL_STEP"
                profile.save()
                return redirect("complete_profile")
        else:
            form = ProfileStep1Form(instance=profile, user=request.user)
        return render(request, "account/complete_profile_step1.html", {"form": form})

    elif profile.profile_completion == "INITIAL_STEP":
        form = ProfileStep2Form(request.POST or None, instance=profile)
        if request.method == "POST":
            if "send_code" in request.POST:
                if form.is_valid():
                    form.save()
                    form.send_verification_code()
                    code_sent = True
            elif "verify_code" in request.POST:
                code_sent = True
                form = ProfileStep2Form(request.POST, instance=profile, code_sent=True)
                if form.is_valid():
                    if form.verify_code():
                        profile.profile_completion = "COMPLETE"
                        profile.save()
                        return profile_congrats(request)
                    else:
                        error_message = "Código inválido. Por favor, intenta de nuevo."
            else:
                form = ProfileStep2Form(request.POST, instance=profile, code_sent=True)

        return render(
            request,
            "account/complete_profile_step2.html",
            {
                "form": form,
                "error_message": error_message,
                "code_sent": code_sent,
                "profile": profile,
            },
        )
    else:
        return redirect("home")


@login_required
def profile_congrats(request):
    user = request.user
    executed_pending_transfers = (
        NewTicketTransfer.objects.filter(tx_to_email__iexact=user.email, status="COMPLETED")
        .all()
    )

    if executed_pending_transfers.exists():
        return render(request, "account/profile_congrats_with_tickets.html")
    else:
        return render(request, "account/profile_congrats.html")


def verification_congrats(request):
    return render(request, "account/verification_congrats.html")


@login_required
def volunteering(request, event_slug=None):
    # Get the specific event from slug
    if event_slug:
        current_event = Event.get_by_slug(event_slug)
        if not current_event:
            raise Http404("Event not found")
    else:
        current_event = Event.get_main_event()
    
    # Get ticket for the specific event
    ticket = get_object_or_404(NewTicket, holder=request.user, owner=request.user, event=current_event)
    show_congrats = False

    if request.method == "POST":
        if ticket.event.volunteer_period() is False:
            raise Http404
        form = VolunteeringForm(request.POST, instance=ticket)
        if form.is_valid():
            form.save()
            show_congrats = True
    else:
        form = VolunteeringForm(instance=ticket)
        show_congrats = False

    # Get events where user has tickets, prioritizing main event
    user_events = Event.get_active_events().filter(
        newticket__holder=request.user
    ).distinct().order_by('-is_main', 'name')

    return render(
        request, 
        "mi_fuego/my_tickets/volunteering.html",
        {
            "form": form,
            "show_congrats": show_congrats,
            "event": current_event,
            "active_events": user_events,
            "nav_primary": "volunteering",
            "nav_secondary": "volunteering",
            "my_ticket": ticket.get_dto(user=request.user) if ticket else None,
            "now": timezone.now(),
        }
    )


@login_required
def my_orders_view(request):
    """Show user's order history"""
    # Get all orders for the current user
    orders = Order.objects.filter(
        user=request.user
    ).order_by('-created_at')
    
    # Get the main event for context
    main_event = Event.get_main_event()
    
    # Get events where user has tickets, prioritizing main event
    user_events = Event.get_active_events().filter(
        newticket__holder=request.user
    ).distinct().order_by('-is_main', 'name')
    
    # Get the first ticket for the main event (for backward compatibility)
    my_ticket = NewTicket.objects.filter(
        holder=request.user, event=main_event, owner=request.user
    ).first() if main_event else None
    
    return render(
        request,
        "mi_fuego/my_tickets/my_orders.html",
        {
            "orders": orders,
            "event": main_event,
            "active_events": user_events,
            "nav_primary": "orders",
            "nav_secondary": "my_orders",
            "my_ticket": my_ticket.get_dto(user=request.user) if my_ticket else None,
            "now": timezone.now(),
        },
    )
