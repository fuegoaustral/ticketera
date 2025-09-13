from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponseNotAllowed, HttpResponseForbidden, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import update_session_auth_hash
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
import json

from events.models import Event
from events.utils import get_event_from_request
from tickets.models import NewTicket, NewTicketTransfer, Order
from .forms import ProfileStep1Form, ProfileStep2Form, VolunteeringForm, ProfileUpdateForm, CustomPasswordChangeForm, AddEmailForm, PhoneUpdateForm


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
def profile_view(request):
    """Vista completa para la página de perfil del usuario"""
    from allauth.account.models import EmailAddress
    from allauth.account.utils import send_email_confirmation
    
    user = request.user
    profile = user.profile
    
    # Get all email addresses for the user
    email_addresses = EmailAddress.objects.filter(user=user).order_by('-primary', 'email')
    
    # Initialize forms
    profile_form = ProfileUpdateForm(instance=profile, user=user)
    password_form = CustomPasswordChangeForm()
    add_email_form = AddEmailForm()
    
    # Handle profile update
    if request.method == 'POST':
        if 'update_profile' in request.POST:
            profile_form = ProfileUpdateForm(request.POST, instance=profile, user=user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, 'Tu perfil ha sido actualizado exitosamente.')
                return redirect('profile')
        
        # Handle password change
        elif 'change_password' in request.POST:
            password_form = CustomPasswordChangeForm(request.POST)
            if password_form.is_valid():
                new_password = password_form.cleaned_data['password1']
                user.set_password(new_password)
                user.save()
                update_session_auth_hash(request, user)  # Important!
                messages.success(request, 'Contraseña actualizada exitosamente.')
            return redirect('profile')
        
        # Handle add email
        elif 'add_email' in request.POST:
            add_email_form = AddEmailForm(request.POST)
            if add_email_form.is_valid():
                email = add_email_form.cleaned_data['email']
                # Check if email already exists
                if EmailAddress.objects.filter(email=email).exists():
                    messages.error(request, 'Este email ya está registrado.')
                else:
                    # Create new email address
                    email_address = EmailAddress.objects.create(
                        user=user,
                        email=email,
                        primary=False,
                        verified=False
                    )
                    # Send confirmation email
                    send_email_confirmation(request, user, email=email)
                    messages.success(request, f'Se ha enviado un email de confirmación a {email}.')
                return redirect('profile')
        
        # Handle set primary email
        elif 'set_primary' in request.POST:
            email_id = request.POST.get('email_id')
            try:
                email_address = EmailAddress.objects.get(id=email_id, user=user)
                if email_address.verified:
                    # Set all other emails as non-primary
                    EmailAddress.objects.filter(user=user).update(primary=False)
                    # Set this one as primary
                    email_address.primary = True
                    email_address.save()
                    messages.success(request, f'{email_address.email} es ahora tu email principal.')
                else:
                    messages.error(request, 'Solo puedes establecer como principal un email verificado.')
            except EmailAddress.DoesNotExist:
                messages.error(request, 'Email no encontrado.')
            return redirect('profile')
        
        # Handle remove email
        elif 'remove_email' in request.POST:
            email_id = request.POST.get('email_id')
            try:
                email_address = EmailAddress.objects.get(id=email_id, user=user)
                if not email_address.primary:
                    email_address.delete()
                    messages.success(request, f'Email {email_address.email} eliminado exitosamente.')
                else:
                    messages.error(request, 'No puedes eliminar tu email principal.')
            except EmailAddress.DoesNotExist:
                messages.error(request, 'Email no encontrado.')
            return redirect('profile')
        
    
    context = {
        'profile_form': profile_form,
        'password_form': password_form,
        'add_email_form': add_email_form,
        'email_addresses': email_addresses,
        'user': user,
        'profile': profile,
    }
    
    return render(request, "mi_fuego/profile.html", context)


@login_required
@require_POST
def send_phone_code_ajax(request):
    """Vista AJAX para enviar código de verificación SMS"""
    try:
        data = json.loads(request.body)
        phone = data.get('phone')
        
        if not phone:
            return JsonResponse({'success': False, 'error': 'Número de teléfono requerido'})
        
        # Create form instance to use Twilio methods
        profile = request.user.profile
        form_data = {'phone': phone}
        phone_form = PhoneUpdateForm(form_data, instance=profile)
        
        if phone_form.is_valid():
            try:
                phone_form.send_verification_code()
                return JsonResponse({
                    'success': True, 
                    'message': 'Código de verificación enviado por SMS'
                })
            except Exception as e:
                return JsonResponse({
                    'success': False, 
                    'error': f'Error al enviar el código: {str(e)}'
                })
        else:
            errors = phone_form.errors.get('phone', [])
            return JsonResponse({
                'success': False, 
                'error': errors[0] if errors else 'Número de teléfono inválido'
            })
            
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Datos inválidos'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': 'Error interno del servidor'})


@login_required
@require_POST
def verify_phone_code_ajax(request):
    """Vista AJAX para verificar código SMS y actualizar teléfono"""
    try:
        data = json.loads(request.body)
        phone = data.get('phone')
        code = data.get('code')
        
        if not phone or not code:
            return JsonResponse({'success': False, 'error': 'Teléfono y código requeridos'})
        
        # Create form instance to use Twilio methods
        profile = request.user.profile
        form_data = {'phone': phone, 'code': code}
        phone_form = PhoneUpdateForm(form_data, instance=profile, code_sent=True)
        
        if phone_form.is_valid():
            if phone_form.verify_code():
                phone_form.save()
                return JsonResponse({
                    'success': True, 
                    'message': 'Número de teléfono actualizado exitosamente',
                    'new_phone': phone
                })
            else:
                return JsonResponse({
                    'success': False, 
                    'error': 'Código de verificación inválido'
                })
        else:
            errors = []
            if phone_form.errors.get('phone'):
                errors.extend(phone_form.errors['phone'])
            if phone_form.errors.get('code'):
                errors.extend(phone_form.errors['code'])
            if phone_form.non_field_errors():
                errors.extend(phone_form.non_field_errors())
            
            return JsonResponse({
                'success': False, 
                'error': errors[0] if errors else 'Datos inválidos'
            })
            
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Datos inválidos'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': 'Error interno del servidor'})


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


@login_required
def my_events_view(request):
    """Show events that the user administers"""
    # Get events where the user is an admin
    admin_events = Event.objects.filter(
        admins=request.user
    ).order_by('-is_main', 'name')
    
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
        "mi_fuego/my_events.html",
        {
            "admin_events": admin_events,
            "event": main_event,
            "active_events": user_events,
            "nav_primary": "events",
            "nav_secondary": "my_events",
            "my_ticket": my_ticket.get_dto(user=request.user) if my_ticket else None,
            "now": timezone.now(),
        },
    )


@login_required
def event_admin_view(request, event_slug):
    """Show admin dashboard for a specific event"""
    from django.db import connection
    from decimal import Decimal
    
    # Get the event and check if user is admin
    try:
        event = Event.objects.get(slug=event_slug)
    except Event.DoesNotExist:
        raise Http404("Event not found")
    
    # Check if user is admin of this event
    if not event.admins.filter(id=request.user.id).exists():
        return HttpResponseForbidden("You don't have permission to view this event")
    
    # Get tickets sold data for this specific event
    with connection.cursor() as cursor:
        query = """
            SELECT 
                COALESCE(SUM(tot.quantity), 0) as tickets_sold,
                COALESCE(SUM(too.amount - COALESCE(too.donation_art, 0) - COALESCE(too.donation_venue, 0) - COALESCE(too.donation_grant, 0)), 0) as ticket_revenue,
                COALESCE(SUM(too.donation_art), 0) as donations_art,
                COALESCE(SUM(too.donation_venue), 0) as donations_venue,
                COALESCE(SUM(too.donation_grant), 0) as donations_grant,
                COALESCE(SUM(too.amount), 0) as total_revenue,
                COALESCE(SUM(too.net_received_amount), 0) as net_received_amount,
                COUNT(DISTINCT too.id) as total_orders
            FROM tickets_order too
            LEFT JOIN tickets_orderticket tot ON too.id = tot.order_id
            WHERE too.event_id = %s AND too.status = 'CONFIRMED'
        """
        cursor.execute(query, [event.id])
        result = cursor.fetchone()
    
    # Get ticket type breakdown for this specific event
    with connection.cursor() as cursor:
        ticket_type_query = """
            SELECT 
                tt.name as ticket_type_name,
                tt.emoji as ticket_type_emoji,
                tt.color as ticket_type_color,
                COALESCE(SUM(tot.quantity), 0) as quantity_sold,
                COALESCE(SUM(tot.quantity * COALESCE(tt.price, 0)), 0) as gross_amount,
                COALESCE(SUM(tot.quantity * COALESCE(tt.price_with_coupon, tt.price, 0)), 0) as gross_amount_with_coupon
            FROM tickets_orderticket tot
            INNER JOIN tickets_order too ON tot.order_id = too.id
            INNER JOIN tickets_tickettype tt ON tot.ticket_type_id = tt.id
            WHERE too.event_id = %s AND too.status = 'CONFIRMED'
            GROUP BY tt.id, tt.name, tt.emoji, tt.color, tt.price, tt.price_with_coupon
            ORDER BY tt.cardinality, tt.price
        """
        cursor.execute(ticket_type_query, [event.id])
        ticket_type_results = cursor.fetchall()
    
    # Get tickets used count for this specific event
    with connection.cursor() as cursor:
        tickets_used_query = """
            SELECT COUNT(*) as tickets_used
            FROM tickets_newticket nt
            INNER JOIN tickets_order too ON nt.order_id = too.id
            WHERE too.event_id = %s AND nt.is_used = true
        """
        cursor.execute(tickets_used_query, [event.id])
        tickets_used_result = cursor.fetchone()
        tickets_used = tickets_used_result[0] if tickets_used_result else 0
    
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
    
    # Calculate percentage sold
    percentage_sold = 0
    if event.max_tickets and event.max_tickets > 0:
        percentage_sold = (result[0] / event.max_tickets) * 100
    
    # Calculate proportional distribution of commissions
    total_revenue = result[5] or Decimal('0')
    net_received_amount = result[6] or Decimal('0')
    total_commissions = total_revenue - net_received_amount
    
    # Calculate net amounts for each category
    ticket_revenue = result[1] or Decimal('0')
    donations_art = result[2] or Decimal('0')
    donations_venue = result[3] or Decimal('0')
    donations_grant = result[4] or Decimal('0')
    
    # Calculate proportional commission distribution
    if total_revenue > 0:
        ticket_commission = (ticket_revenue / total_revenue) * total_commissions
        donations_art_commission = (donations_art / total_revenue) * total_commissions
        donations_venue_commission = (donations_venue / total_revenue) * total_commissions
        donations_grant_commission = (donations_grant / total_revenue) * total_commissions
    else:
        ticket_commission = donations_art_commission = donations_venue_commission = donations_grant_commission = Decimal('0')
    
    # Calculate net amounts (gross - proportional commission)
    # Round to 2 decimal places for ticket revenue, 0 decimal places for donations
    ticket_revenue_net = (ticket_revenue - ticket_commission).quantize(Decimal('0.01'))
    
    # For donations, let's not round to integers to see the real proportional calculation
    donations_art_net = donations_art - donations_art_commission
    donations_venue_net = donations_venue - donations_venue_commission
    donations_grant_net = donations_grant - donations_grant_commission
    
    # Process ticket type breakdown data
    ticket_type_breakdown = []
    total_ticket_type_gross = Decimal('0')
    total_ticket_type_net = Decimal('0')
    
    for row in ticket_type_results:
        ticket_type_name, emoji, color, quantity_sold, gross_amount, gross_amount_with_coupon = row
        gross_amount_decimal = Decimal(str(gross_amount))
        
        # Calculate proportional commission for this ticket type
        if total_revenue > 0:
            ticket_type_commission = (gross_amount_decimal / total_revenue) * total_commissions
        else:
            ticket_type_commission = Decimal('0')
        
        # Round to 2 decimal places for ticket types
        net_amount = (gross_amount_decimal - ticket_type_commission).quantize(Decimal('0.01'))
        
        total_ticket_type_gross += gross_amount_decimal
        total_ticket_type_net += net_amount
        
        ticket_type_breakdown.append({
            'name': ticket_type_name,
            'emoji': emoji,
            'color': color,
            'quantity_sold': quantity_sold,
            'gross_amount': gross_amount_decimal,
            'net_amount': net_amount,
            'commission': ticket_type_commission,
        })
    
    # Debug: Print ticket type totals
    print(f"DEBUG - Ticket Type Gross Total: {total_ticket_type_gross}")
    print(f"DEBUG - Ticket Type Net Total: {total_ticket_type_net}")
    print(f"DEBUG - Ticket Revenue from main query: {ticket_revenue}")
    print(f"DEBUG - Ticket Revenue Net from main query: {ticket_revenue_net}")
    
    context = {
        "event": event,
        "main_event": main_event,
        "active_events": user_events,
        "nav_primary": "events",
        "nav_secondary": f"event_admin_{event.slug}",
        "my_ticket": my_ticket.get_dto(user=request.user) if my_ticket else None,
        "now": timezone.now(),
        "stats": {
            "tickets_sold": result[0] or 0,
            "tickets_used": tickets_used,
            "ticket_revenue": ticket_revenue,
            "donations_art": donations_art,
            "donations_venue": donations_venue,
            "donations_grant": donations_grant,
            "total_revenue": total_revenue,
            "net_received_amount": net_received_amount,
            "total_orders": result[7] or 0,
            "percentage_sold": percentage_sold,
            "ticket_revenue_net": ticket_revenue_net,
            "donations_art_net": donations_art_net,
            "donations_venue_net": donations_venue_net,
            "donations_grant_net": donations_grant_net,
            # Debug info for commission calculation
            "total_commissions": total_commissions,
            "ticket_commission": ticket_commission,
            "donations_art_commission": donations_art_commission,
            "donations_venue_commission": donations_venue_commission,
            "donations_grant_commission": donations_grant_commission,
            "commission_percentage": (total_commissions / total_revenue * 100) if total_revenue > 0 else 0,
        },
        "ticket_type_breakdown": ticket_type_breakdown,
    }
    
    return render(request, "mi_fuego/event_admin.html", context)
