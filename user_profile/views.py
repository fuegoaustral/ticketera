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
from django.contrib.auth.models import User
from django.conf import settings
import json

from events.models import Event
from events.utils import get_event_from_request
from tickets.models import NewTicket, NewTicketTransfer, Order
from .forms import ProfileStep1Form, ProfileStep2Form, VolunteeringForm, ProfileUpdateForm, CustomPasswordChangeForm, AddEmailForm, PhoneUpdateForm, CajaEmitirBonoForm


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
    password_form = CustomPasswordChangeForm(user=user)
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
            password_form = CustomPasswordChangeForm(user=user, data=request.POST)
            if password_form.is_valid():
                password_form.save()
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
        
        # Handle resend confirmation email
        elif 'send_confirmation' in request.POST:
            email_id = request.POST.get('email_id')
            try:
                email_address = EmailAddress.objects.get(id=email_id, user=user)
                if not email_address.verified:
                    # Send confirmation email (allauth will show its own message)
                    send_email_confirmation(request, user, email=email_address.email)
                else:
                    messages.error(request, 'Este email ya está verificado.')
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
        
        # Debug logging
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"SEND CODE - Phone received: {phone}")
        
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
        
        # Debug logging
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"VERIFY CODE - Phone received: {phone}, Code: {code}")
        
        # Create form instance to use Twilio methods
        profile = request.user.profile
        form_data = {'phone': phone, 'code': code}
        phone_form = PhoneUpdateForm(form_data, instance=profile, code_sent=True)
        
        if phone_form.is_valid():
            try:
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
            except Exception as e:
                return JsonResponse({
                    'success': False, 
                    'error': f'Error al verificar el código: {str(e)}'
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
    
    # Check if user has scanner access for this event (admins automatically have scanner access)
    has_scanner_access = (event.admins.filter(id=request.user.id).exists() or 
                         event.access_scanner.filter(id=request.user.id).exists())
    
    # Get tickets sold data for this specific event
    with connection.cursor() as cursor:
        query = """
            SELECT 
                -- Total tickets: count from newticket table, including ALL tickets for accurate statistics
                COUNT(DISTINCT nt.id) as tickets_sold,
                -- Get amounts from orders without duplication
                (SELECT COALESCE(SUM(amount - COALESCE(donation_art, 0) - COALESCE(donation_venue, 0) - COALESCE(donation_grant, 0)), 0) FROM tickets_order WHERE event_id = %s AND status = 'CONFIRMED') as ticket_revenue,
                (SELECT COALESCE(SUM(donation_art), 0) FROM tickets_order WHERE event_id = %s AND status = 'CONFIRMED') as donations_art,
                (SELECT COALESCE(SUM(donation_venue), 0) FROM tickets_order WHERE event_id = %s AND status = 'CONFIRMED') as donations_venue,
                (SELECT COALESCE(SUM(donation_grant), 0) FROM tickets_order WHERE event_id = %s AND status = 'CONFIRMED') as donations_grant,
                (SELECT COALESCE(SUM(amount), 0) FROM tickets_order WHERE event_id = %s AND status = 'CONFIRMED') as total_revenue,
                (SELECT COALESCE(SUM(net_received_amount), 0) FROM tickets_order WHERE event_id = %s AND status = 'CONFIRMED') as net_received_amount,
                COUNT(DISTINCT too.id) as total_orders,
                -- Caja orders (generated_by_admin_user_id is not null)
                COUNT(DISTINCT CASE WHEN too.generated_by_admin_user_id IS NOT NULL THEN nt.id END) as caja_tickets_sold,
                (SELECT COALESCE(SUM(amount - COALESCE(donation_art, 0) - COALESCE(donation_venue, 0) - COALESCE(donation_grant, 0)), 0) FROM tickets_order WHERE event_id = %s AND status = 'CONFIRMED' AND generated_by_admin_user_id IS NOT NULL) as caja_ticket_revenue,
                (SELECT COALESCE(SUM(amount), 0) FROM tickets_order WHERE event_id = %s AND status = 'CONFIRMED' AND generated_by_admin_user_id IS NOT NULL) as caja_total_revenue,
                (SELECT COALESCE(SUM(net_received_amount), 0) FROM tickets_order WHERE event_id = %s AND status = 'CONFIRMED' AND generated_by_admin_user_id IS NOT NULL) as caja_net_received_amount,
                COUNT(DISTINCT CASE WHEN too.generated_by_admin_user_id IS NOT NULL THEN too.id END) as caja_total_orders,
                -- Regular orders (generated_by_admin_user_id is null) - only from orderticket table
                COUNT(DISTINCT CASE WHEN too.generated_by_admin_user_id IS NULL THEN nt.id END) as regular_tickets_sold,
                (SELECT COALESCE(SUM(amount - COALESCE(donation_art, 0) - COALESCE(donation_venue, 0) - COALESCE(donation_grant, 0)), 0) FROM tickets_order WHERE event_id = %s AND status = 'CONFIRMED' AND generated_by_admin_user_id IS NULL) as regular_ticket_revenue,
                (SELECT COALESCE(SUM(amount), 0) FROM tickets_order WHERE event_id = %s AND status = 'CONFIRMED' AND generated_by_admin_user_id IS NULL) as regular_total_revenue,
                (SELECT COALESCE(SUM(net_received_amount), 0) FROM tickets_order WHERE event_id = %s AND status = 'CONFIRMED' AND generated_by_admin_user_id IS NULL) as regular_net_received_amount,
                COUNT(DISTINCT CASE WHEN too.generated_by_admin_user_id IS NULL THEN too.id END) as regular_total_orders
            FROM tickets_order too
            LEFT JOIN tickets_orderticket tot ON too.id = tot.order_id
            LEFT JOIN tickets_newticket nt ON too.id = nt.order_id
            LEFT JOIN tickets_tickettype tt ON nt.ticket_type_id = tt.id
            WHERE too.event_id = %s AND too.status = 'CONFIRMED'
        """
        cursor.execute(query, [event.id] * 12 + [event.id])
        result = cursor.fetchone()
        
        
    
    # Get ticket type breakdown for this specific event
    with connection.cursor() as cursor:
        ticket_type_query = """
            SELECT 
                tt.name as ticket_type_name,
                tt.emoji as ticket_type_emoji,
                tt.color as ticket_type_color,
                -- Total quantity and amount (from orderticket table only)
                COALESCE(SUM(tot.quantity), 0) as quantity_sold,
                COALESCE(SUM(tot.quantity * COALESCE(tt.price, 0)), 0) as gross_amount,
                COALESCE(SUM(tot.quantity * COALESCE(tt.price_with_coupon, tt.price, 0)), 0) as gross_amount_with_coupon,
                -- Caja orders breakdown
                COALESCE(SUM(CASE WHEN too.generated_by_admin_user_id IS NOT NULL THEN tot.quantity ELSE 0 END), 0) as caja_quantity_sold,
                COALESCE(SUM(CASE WHEN too.generated_by_admin_user_id IS NOT NULL THEN (tot.quantity * COALESCE(tt.price, 0)) ELSE 0 END), 0) as caja_gross_amount,
                -- Regular orders breakdown
                COALESCE(SUM(CASE WHEN too.generated_by_admin_user_id IS NULL THEN tot.quantity ELSE 0 END), 0) as regular_quantity_sold,
                COALESCE(SUM(CASE WHEN too.generated_by_admin_user_id IS NULL THEN (tot.quantity * COALESCE(tt.price, 0)) ELSE 0 END), 0) as regular_gross_amount
            FROM tickets_tickettype tt
            LEFT JOIN tickets_orderticket tot ON tt.id = tot.ticket_type_id
            LEFT JOIN tickets_order too ON tot.order_id = too.id
            WHERE tt.event_id = %s AND (too.status = 'CONFIRMED' OR too.status IS NULL)
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
    
    # Get caja orders breakdown by payment method - separate queries for clarity
    with connection.cursor() as cursor:
        # First, get the ticket counts by payment method
        caja_ticket_counts_query = """
            SELECT 
                too.order_type,
                COUNT(DISTINCT nt.id) as tickets_sold
            FROM tickets_order too
            LEFT JOIN tickets_newticket nt ON too.id = nt.order_id
            WHERE too.event_id = %s AND too.status = 'CONFIRMED' AND too.generated_by_admin_user_id IS NOT NULL
            GROUP BY too.order_type
            ORDER BY too.order_type
        """
        cursor.execute(caja_ticket_counts_query, [event.id])
        caja_ticket_counts = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Then, get the amounts by payment method
        caja_amounts_query = """
            SELECT 
                too.order_type,
                COUNT(DISTINCT too.id) as order_count,
                COALESCE(SUM(too.amount), 0) as total_amount,
                COALESCE(SUM(too.net_received_amount), 0) as net_amount
            FROM tickets_order too
            WHERE too.event_id = %s AND too.status = 'CONFIRMED' AND too.generated_by_admin_user_id IS NOT NULL
            GROUP BY too.order_type
            ORDER BY too.order_type
        """
        cursor.execute(caja_amounts_query, [event.id])
        caja_amounts_results = cursor.fetchall()
        
        # Combine the data
        caja_payment_method_results = []
        for row in caja_amounts_results:
            order_type, order_count, total_amount, net_amount = row
            tickets_sold = caja_ticket_counts.get(order_type, 0)
            caja_payment_method_results.append((order_type, order_count, tickets_sold, total_amount, net_amount))
    
    # Get the main event for context
    main_event = Event.get_main_event()
    
    # Get events where the user is an admin
    admin_events = Event.objects.filter(
        admins=request.user
    ).order_by('-is_main', 'name')
    
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
        total_tickets_sold = result[0] or 0  # tickets_sold (already includes both regular and caja)
        percentage_sold = (total_tickets_sold / event.max_tickets) * 100
    
    # Calculate proportional distribution of commissions
    # Only calculate commissions for regular orders (exclude caja orders)
    regular_total_revenue = result[15] or Decimal('0')  # regular_total_revenue
    regular_net_received_amount = result[16] or Decimal('0')  # regular_net_received_amount
    regular_commissions = regular_total_revenue - regular_net_received_amount
    
    # Caja orders data (no MP commissions)
    caja_total_revenue = result[10] or Decimal('0')  # caja_total_revenue
    caja_net_received_amount = result[11] or Decimal('0')  # caja_net_received_amount
    
    # Total revenue includes both regular and caja orders
    # We'll calculate this after processing caja payment methods
    total_revenue = result[5] or Decimal('0')
    net_received_amount = result[6] or Decimal('0')
    
    # Calculate net amounts for each category (all orders)
    ticket_revenue = result[1] or Decimal('0')
    donations_art = result[2] or Decimal('0')
    donations_venue = result[3] or Decimal('0')
    donations_grant = result[4] or Decimal('0')
    
    # Calculate proportional commission distribution (only for regular orders)
    # Commissions should only apply to ticket revenue, not donations
    regular_ticket_revenue = result[14] or Decimal('0')  # regular_ticket_revenue
    
    
    # The issue might be that we need to calculate commissions differently
    # Let's use the actual net received amount to calculate the commission rate
    if regular_total_revenue > 0:
        # Calculate commission rate based on total revenue (including donations)
        commission_rate = regular_commissions / regular_total_revenue
        ticket_commission = regular_commissions  # All commissions come from total sales
    else:
        commission_rate = Decimal('0')
        ticket_commission = Decimal('0')
    
    # Calculate proportional commission for donations (they do have commissions in MercadoPago)
    if regular_total_revenue > 0:
        donations_art_commission = (donations_art / regular_total_revenue) * regular_commissions
        donations_venue_commission = (donations_venue / regular_total_revenue) * regular_commissions
        donations_grant_commission = (donations_grant / regular_total_revenue) * regular_commissions
    else:
        donations_art_commission = donations_venue_commission = donations_grant_commission = Decimal('0')
    
    # Calculate net amounts (gross - proportional commission)
    # Round to 2 decimal places for ticket revenue, 0 decimal places for donations
    ticket_revenue_net = (ticket_revenue - ticket_commission).quantize(Decimal('0.01'))
    regular_ticket_revenue_net = (regular_ticket_revenue - ticket_commission).quantize(Decimal('0.01'))
    
    # Donations have commissions applied
    donations_art_net = (donations_art - donations_art_commission).quantize(Decimal('0.01'))
    donations_venue_net = (donations_venue - donations_venue_commission).quantize(Decimal('0.01'))
    donations_grant_net = (donations_grant - donations_grant_commission).quantize(Decimal('0.01'))
    
    # Process ticket type breakdown data
    ticket_type_breakdown = []
    total_ticket_type_gross = Decimal('0')
    total_ticket_type_net = Decimal('0')
    
    for row in ticket_type_results:
        ticket_type_name, emoji, color, quantity_sold, gross_amount, gross_amount_with_coupon, caja_quantity_sold, caja_gross_amount, regular_quantity_sold, regular_gross_amount = row
        gross_amount_decimal = Decimal(str(gross_amount))
        caja_gross_amount_decimal = Decimal(str(caja_gross_amount))
        regular_gross_amount_decimal = Decimal(str(regular_gross_amount))
        
        
        # Calculate proportional commission for this ticket type (only for regular orders)
        if regular_total_revenue > 0:
            ticket_type_commission = regular_gross_amount_decimal * commission_rate
        else:
            ticket_type_commission = Decimal('0')
        
        # Net amount for regular orders only (caja orders have no commissions)
        regular_net_amount = (regular_gross_amount_decimal - ticket_type_commission).quantize(Decimal('0.01'))
        
        # Total net amount = regular net + caja gross (caja has no commissions)
        total_net_amount = (regular_net_amount + caja_gross_amount_decimal).quantize(Decimal('0.01'))
        
        total_ticket_type_gross += gross_amount_decimal
        total_ticket_type_net += total_net_amount
        
        ticket_type_breakdown.append({
            'name': ticket_type_name,
            'emoji': emoji,
            'color': color,
            'quantity_sold': quantity_sold,
            'gross_amount': gross_amount_decimal,
            'net_amount': total_net_amount,
            'regular_net_amount': regular_net_amount,
            'commission': ticket_type_commission,
            'caja_quantity_sold': caja_quantity_sold,
            'caja_gross_amount': caja_gross_amount_decimal,
            'regular_quantity_sold': regular_quantity_sold,
            'regular_gross_amount': regular_gross_amount_decimal,
        })
    
    # Process caja payment method breakdown
    caja_payment_method_breakdown = []
    caja_payment_method_total_amount = Decimal('0')
    caja_payment_method_net_amount = Decimal('0')
    caja_payment_method_tickets_sold = 0
    
    for row in caja_payment_method_results:
        order_type, order_count, tickets_sold, total_amount, net_amount = row
        # Map order types to display names
        order_type_display = {
            'CASH_ONSITE': 'Efectivo',
            'LOCAL_TRANSFER': 'Transferencia',
            'INTERNATIONAL_TRANSFER': 'Transferencia Internacional',
            'OTHER': 'Otro'
        }.get(order_type, order_type)
        
        total_amount_decimal = Decimal(str(total_amount))
        net_amount_decimal = Decimal(str(net_amount))
        
        caja_payment_method_total_amount += total_amount_decimal
        caja_payment_method_net_amount += net_amount_decimal
        caja_payment_method_tickets_sold += tickets_sold
        
        caja_payment_method_breakdown.append({
            'order_type': order_type,
            'order_type_display': order_type_display,
            'order_count': order_count,
            'tickets_sold': tickets_sold,
            'total_amount': total_amount_decimal,
            'net_amount': net_amount_decimal,
        })
    
    # Use the original total from the main query to avoid duplication issues
    # The main query already correctly separates regular and caja orders
    total_revenue = result[5] or Decimal('0')  # total_revenue from main query
    net_received_amount = result[6] or Decimal('0')  # net_received_amount from main query
    
    context = {
        "event": event,
        "main_event": main_event,
        "admin_events": admin_events,
        "active_events": user_events,
        "nav_primary": "events",
        "nav_secondary": f"event_admin_{event.slug}",
        "my_ticket": my_ticket.get_dto(user=request.user) if my_ticket else None,
        "now": timezone.now(),
        "has_scanner_access": has_scanner_access,
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
            "regular_ticket_revenue_net": regular_ticket_revenue_net,
            "donations_art_net": donations_art_net,
            "donations_venue_net": donations_venue_net,
            "donations_grant_net": donations_grant_net,
            # Venue occupancy statistics
            "venue_occupancy": event.venue_occupancy,
            "venue_capacity": event.venue_capacity,
            "occupancy_percentage": event.occupancy_percentage,
            "attendees_left": event.attendees_left,
            # Caja orders data - use calculated totals from payment method breakdown
            "caja_tickets_sold": caja_payment_method_tickets_sold,
            "caja_ticket_revenue": result[9] or 0,
            "caja_total_revenue": caja_payment_method_total_amount,
            "caja_net_received_amount": caja_payment_method_net_amount,
            "caja_total_orders": result[12] or 0,
            # Regular orders data
            "regular_tickets_sold": result[13] or 0,
            "regular_ticket_revenue": result[14] or 0,
            "regular_total_revenue": regular_total_revenue,
            "regular_net_received_amount": regular_net_received_amount,
            "regular_total_orders": result[17] or 0,
            # Debug info for commission calculation
            "total_commissions": regular_commissions,
            "ticket_commission": ticket_commission,
            "donations_art_commission": donations_art_commission,
            "donations_venue_commission": donations_venue_commission,
            "donations_grant_commission": donations_grant_commission,
            "commission_percentage": (commission_rate * 100) if regular_total_revenue > 0 else 0,
        },
        "ticket_type_breakdown": ticket_type_breakdown,
        "caja_payment_method_breakdown": caja_payment_method_breakdown,
    }
    
    return render(request, "mi_fuego/event_admin.html", context)


@login_required
def puerta_admin_view(request, event_slug):
    """Manage scanner access for a specific event"""
    # Get the event and check if user is admin
    try:
        event = Event.objects.get(slug=event_slug)
    except Event.DoesNotExist:
        raise Http404("Event not found")
    
    # Check if user is admin of this event
    if not event.admins.filter(id=request.user.id).exists():
        return HttpResponseForbidden("You don't have permission to manage scanner access for this event")
    
    # Handle form submissions
    if request.method == 'POST':
        if 'add_user' in request.POST:
            email = request.POST.get('email', '').strip()
            if email:
                try:
                    user = User.objects.get(email__iexact=email)
                    if not event.access_scanner.filter(id=user.id).exists():
                        event.access_scanner.add(user)
                        messages.success(request, f'Usuario {email} agregado al scanner exitosamente.')
                    else:
                        messages.warning(request, f'El usuario {email} ya tiene acceso al scanner.')
                except User.DoesNotExist:
                    messages.error(request, f'No se encontró un usuario con el email {email}.')
            else:
                messages.error(request, 'Por favor ingresa un email válido.')
        
        elif 'remove_user' in request.POST:
            user_id = request.POST.get('user_id')
            if user_id:
                try:
                    user = User.objects.get(id=user_id)
                    event.access_scanner.remove(user)
                    messages.success(request, f'Usuario {user.email} removido del scanner exitosamente.')
                except User.DoesNotExist:
                    messages.error(request, 'Usuario no encontrado.')
        
        return redirect('puerta_admin', event_slug=event_slug)
    
    # Get current scanner users (including admins automatically)
    scanner_users = event.access_scanner.all().order_by('email')
    admin_users = event.admins.all().order_by('email')
    
    # Combine scanner users and admin users, removing duplicates
    all_scanner_users = list(scanner_users) + [admin for admin in admin_users if admin not in scanner_users]
    
    # Get the main event for context
    main_event = Event.get_main_event()
    
    # Get events where the user is an admin
    admin_events = Event.objects.filter(
        admins=request.user
    ).order_by('-is_main', 'name')
    
    # Get events where user has tickets, prioritizing main event
    user_events = Event.get_active_events().filter(
        newticket__holder=request.user
    ).distinct().order_by('-is_main', 'name')
    
    # Get the first ticket for the main event (for backward compatibility)
    my_ticket = NewTicket.objects.filter(
        holder=request.user, event=main_event, owner=request.user
    ).first() if main_event else None
    
    context = {
        "event": event,
        "main_event": main_event,
        "admin_events": admin_events,
        "active_events": user_events,
        "nav_primary": "events",
        "nav_secondary": f"puerta_admin_{event.slug}",
        "my_ticket": my_ticket.get_dto(user=request.user) if my_ticket else None,
        "now": timezone.now(),
        "scanner_users": scanner_users,
        "admin_users": admin_users,
        "all_scanner_users": all_scanner_users,
    }
    
    return render(request, "mi_fuego/puerta_admin.html", context)


@login_required
def caja_config_view(request, event_slug):
    """Configure ticket types for caja (show_in_caja and ignore_max_amount settings)"""
    from tickets.models import TicketType
    
    # Get the event and check if user is admin
    try:
        event = Event.objects.get(slug=event_slug)
    except Event.DoesNotExist:
        raise Http404("Event not found")
    
    # Check if user is admin or has caja access for this event
    has_caja_access = (event.admins.filter(id=request.user.id).exists() or 
                      event.access_caja.filter(id=request.user.id).exists())
    if not has_caja_access:
        return HttpResponseForbidden("You don't have permission to access caja for this event")
    
    # Handle POST request for caja access management
    if request.method == 'POST':
        if 'add_caja_user' in request.POST:
            email = request.POST.get('email', '').strip()
            if email:
                try:
                    user = User.objects.get(email__iexact=email)
                    if not event.access_caja.filter(id=user.id).exists() and not event.admins.filter(id=user.id).exists():
                        event.access_caja.add(user)
                        messages.success(request, f'Usuario {email} agregado al acceso de caja exitosamente.')
                    else:
                        messages.warning(request, f'El usuario {email} ya tiene acceso a caja o es admin del evento.')
                except User.DoesNotExist:
                    messages.error(request, f'No se encontró un usuario con el email {email}.')
            else:
                messages.error(request, 'Por favor ingresa un email válido.')
        
        elif 'remove_caja_user' in request.POST:
            user_id = request.POST.get('user_id')
            if user_id:
                try:
                    user = User.objects.get(id=user_id)
                    event.access_caja.remove(user)
                    messages.success(request, f'Usuario {user.email} removido del acceso de caja exitosamente.')
                except User.DoesNotExist:
                    messages.error(request, 'Usuario no encontrado.')
        
        # Handle ticket type settings
        for ticket_type_id, settings in request.POST.items():
            if ticket_type_id.startswith('ticket_type_'):
                try:
                    ticket_type = TicketType.objects.get(
                        id=ticket_type_id.replace('ticket_type_', ''),
                        event=event
                    )
                    
                    # Update show_in_caja setting
                    show_in_caja_key = f'show_in_caja_{ticket_type.id}'
                    ticket_type.show_in_caja = show_in_caja_key in request.POST
                    
                    # Update ignore_max_amount setting
                    ignore_max_amount_key = f'ignore_max_amount_{ticket_type.id}'
                    ticket_type.ignore_max_amount = ignore_max_amount_key in request.POST
                    
                    ticket_type.save()
                    
                except TicketType.DoesNotExist:
                    continue
        
        # Redirect to avoid resubmission
        return redirect('caja_config', event_slug=event.slug)
    
    # Get all ticket types for this event
    ticket_types = TicketType.objects.filter(event=event).order_by('cardinality', 'name')
    
    # Get the main event for context
    main_event = Event.get_main_event()
    
    # Get events where the user is an admin
    admin_events = Event.objects.filter(
        admins=request.user
    ).order_by('-is_main', 'name')
    
    # Get events where user has tickets, prioritizing main event
    user_events = Event.get_active_events().filter(
        newticket__holder=request.user
    ).distinct().order_by('-is_main', 'name')
    
    # Get the first ticket for the main event (for backward compatibility)
    my_ticket = NewTicket.objects.filter(
        holder=request.user, event=main_event, owner=request.user
    ).first() if main_event else None
    
    # Get current caja users (including admins automatically)
    caja_users = event.access_caja.all().order_by('email')
    admin_users = event.admins.all().order_by('email')
    
    # Combine caja users and admin users, removing duplicates
    all_caja_users = list(caja_users) + [admin for admin in admin_users if admin not in caja_users]
    
    context = {
        "event": event,
        "main_event": main_event,
        "admin_events": admin_events,
        "active_events": user_events,
        "nav_primary": "events",
        "nav_secondary": f"caja_config_{event.slug}",
        "my_ticket": my_ticket.get_dto(user=request.user) if my_ticket else None,
        "now": timezone.now(),
        "ticket_types": ticket_types,
        "caja_users": all_caja_users,
    }
    
    return render(request, "mi_fuego/caja_config.html", context)


@login_required
def roles_management_view(request, event_slug):
    """Unified roles management for admins, caja, and scanner access"""
    # Get the event and check if user is admin
    try:
        event = Event.objects.get(slug=event_slug)
    except Event.DoesNotExist:
        raise Http404("Event not found")
    
    # Check if user is admin of this event
    if not event.admins.filter(id=request.user.id).exists():
        return HttpResponseForbidden("You don't have permission to manage roles for this event")
    
    # Handle form submissions
    if request.method == 'POST':
        if 'add_user' in request.POST:
            email = request.POST.get('email', '').strip()
            role_type = request.POST.get('role_type', '')
            
            if email and role_type:
                try:
                    user = User.objects.get(email__iexact=email)
                    
                    # Add user to the specified role
                    if role_type == 'admin':
                        if not event.admins.filter(id=user.id).exists():
                            event.admins.add(user)
                            messages.success(request, f'Usuario {email} agregado como administrador exitosamente.')
                        else:
                            messages.warning(request, f'El usuario {email} ya es administrador del evento.')
                    elif role_type == 'caja':
                        if not event.access_caja.filter(id=user.id).exists() and not event.admins.filter(id=user.id).exists():
                            event.access_caja.add(user)
                            messages.success(request, f'Usuario {email} agregado con acceso a caja exitosamente.')
                        else:
                            messages.warning(request, f'El usuario {email} ya tiene acceso a caja o es administrador.')
                    elif role_type == 'scanner':
                        if not event.access_scanner.filter(id=user.id).exists() and not event.admins.filter(id=user.id).exists():
                            event.access_scanner.add(user)
                            messages.success(request, f'Usuario {email} agregado con acceso al scanner exitosamente.')
                        else:
                            messages.warning(request, f'El usuario {email} ya tiene acceso al scanner o es administrador.')
                            
                except User.DoesNotExist:
                    messages.error(request, f'No se encontró un usuario con el email {email}.')
            else:
                messages.error(request, 'Por favor ingresa un email válido y selecciona un rol.')
        
        elif 'remove_user' in request.POST:
            user_id = request.POST.get('user_id')
            role_type = request.POST.get('role_type')
            
            if user_id and role_type:
                try:
                    user = User.objects.get(id=user_id)
                    
                    if role_type == 'admin':
                        # Don't allow removing the last admin
                        if event.admins.count() > 1:
                            event.admins.remove(user)
                            messages.success(request, f'Usuario {user.email} removido como administrador exitosamente.')
                        else:
                            messages.error(request, 'No se puede remover el último administrador del evento.')
                    elif role_type == 'caja':
                        event.access_caja.remove(user)
                        messages.success(request, f'Usuario {user.email} removido del acceso a caja exitosamente.')
                    elif role_type == 'scanner':
                        event.access_scanner.remove(user)
                        messages.success(request, f'Usuario {user.email} removido del acceso al scanner exitosamente.')
                        
                except User.DoesNotExist:
                    messages.error(request, 'Usuario no encontrado.')
        
        return redirect('roles_management', event_slug=event_slug)
    
    # Get current users for each role
    admin_users = event.admins.all().order_by('email')
    caja_users = event.access_caja.all().order_by('email')
    scanner_users = event.access_scanner.all().order_by('email')
    
    # Get the main event for context
    main_event = Event.get_main_event()
    
    # Get events where the user is an admin
    admin_events = Event.objects.filter(
        admins=request.user
    ).order_by('-is_main', 'name')
    
    # Get events where user has tickets, prioritizing main event
    user_events = Event.get_active_events().filter(
        newticket__holder=request.user
    ).distinct().order_by('-is_main', 'name')
    
    # Get the first ticket for the main event (for backward compatibility)
    my_ticket = NewTicket.objects.filter(
        holder=request.user, event=main_event, owner=request.user
    ).first() if main_event else None
    
    context = {
        "event": event,
        "main_event": main_event,
        "admin_events": admin_events,
        "active_events": user_events,
        "nav_primary": "events",
        "nav_secondary": f"roles_{event.slug}",
        "my_ticket": my_ticket.get_dto(user=request.user) if my_ticket else None,
        "now": timezone.now(),
        "admin_users": admin_users,
        "caja_users": caja_users,
        "scanner_users": scanner_users,
    }
    
    return render(request, "mi_fuego/roles_management.html", context)


@login_required
def event_management_view(request, event_slug):
    """Manage event details and settings"""
    from django.forms import ModelForm
    from django import forms
    from django_ckeditor_5.widgets import CKEditor5Widget
    
    # Get the event and check if user is admin
    try:
        event = Event.objects.get(slug=event_slug)
    except Event.DoesNotExist:
        raise Http404("Event not found")
    
    # Check if user is admin of this event
    if not event.admins.filter(id=request.user.id).exists():
        return HttpResponseForbidden("You don't have permission to manage this event")
    
    # Create form class dynamically
    class EventManagementForm(ModelForm):
        class Meta:
            model = Event
            fields = [
                'name', 'description', 'location', 'location_url',
                'start', 'end', 'header_image',
                'max_tickets', 'venue_capacity',
                'attendee_must_be_registered'
            ]
            widgets = {
                'description': CKEditor5Widget(config_name='extends'),
                'start': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
                'end': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
                'name': forms.TextInput(attrs={'class': 'form-control'}),
                'location': forms.TextInput(attrs={'class': 'form-control'}),
                'location_url': forms.URLInput(attrs={'class': 'form-control'}),
                'max_tickets': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
                'venue_capacity': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
                'attendee_must_be_registered': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            }
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # Format datetime fields for HTML5 datetime-local input
            if self.instance and self.instance.pk:
                if self.instance.start:
                    self.initial['start'] = self.instance.start.strftime('%Y-%m-%dT%H:%M')
                if self.instance.end:
                    self.initial['end'] = self.instance.end.strftime('%Y-%m-%dT%H:%M')
    
    # Handle form submission
    if request.method == 'POST':
        form = EventManagementForm(request.POST, request.FILES, instance=event)
        if form.is_valid():
            event = form.save(commit=False)
            # Use provided slug from form, or auto-generate from name if empty
            provided_slug = request.POST.get('slug', '').strip()
            if provided_slug:
                event.slug = provided_slug
            else:
                from django.utils.text import slugify
                event.slug = slugify(event.name)
            event.save()
            messages.success(request, 'Evento actualizado exitosamente.')
            return redirect('event_management', event_slug=event.slug)
        else:
            messages.error(request, 'Por favor corrige los errores en el formulario.')
    else:
        form = EventManagementForm(instance=event)
    
    # Get the main event for context
    main_event = Event.get_main_event()
    
    # Get events where the user is an admin
    admin_events = Event.objects.filter(
        admins=request.user
    ).order_by('-is_main', 'name')
    
    # Get events where user has tickets, prioritizing main event
    user_events = Event.get_active_events().filter(
        newticket__holder=request.user
    ).distinct().order_by('-is_main', 'name')
    
    # Get the first ticket for the main event (for backward compatibility)
    my_ticket = NewTicket.objects.filter(
        holder=request.user, event=main_event, owner=request.user
    ).first() if main_event else None
    
    context = {
        "event": event,
        "form": form,
        "main_event": main_event,
        "admin_events": admin_events,
        "active_events": user_events,
        "nav_primary": "events",
        "nav_secondary": f"event_management_{event.slug}",
        "my_ticket": my_ticket.get_dto(user=request.user) if my_ticket else None,
        "now": timezone.now(),
    }
    
    return render(request, "mi_fuego/event_management.html", context)


@login_required
def ticket_types_management_view(request, event_slug):
    """Manage ticket types for an event"""
    from django.forms import ModelForm
    from django import forms
    from tickets.models import TicketType
    
    # Get the event and check if user is admin
    try:
        event = Event.objects.get(slug=event_slug)
    except Event.DoesNotExist:
        raise Http404("Event not found")
    
    # Check if user is admin of this event
    if not event.admins.filter(id=request.user.id).exists():
        return HttpResponseForbidden("You don't have permission to manage this event")
    
    # Create form class dynamically
    class TicketTypeForm(ModelForm):
        class Meta:
            model = TicketType
            fields = [
                'name', 'description', 'date_from', 'date_to', 
                'price', 'ticket_count', 'show_in_caja'
            ]
            widgets = {
                'name': forms.TextInput(attrs={'class': 'form-control'}),
                'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
                'date_from': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
                'date_to': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
                'price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'placeholder': '0.00'}),
                'ticket_count': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
                'show_in_caja': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            }
    
    # Handle form submission
    if request.method == 'POST':
        if 'action' in request.POST:
            action = request.POST.get('action')
            
            if action == 'create':
                form = TicketTypeForm(request.POST)
                if form.is_valid():
                    ticket_type = form.save(commit=False)
                    ticket_type.event = event
                    # Set cardinality to be the last one
                    last_cardinality = TicketType.objects.filter(event=event).order_by('-cardinality').first()
                    ticket_type.cardinality = (last_cardinality.cardinality + 1) if last_cardinality and last_cardinality.cardinality else 1
                    ticket_type.save()
                    messages.success(request, f'Ticket type "{ticket_type.name}" created successfully.')
                    return redirect('ticket_types_management', event_slug=event.slug)
                else:
                    messages.error(request, 'Please correct the errors below.')
            
            elif action == 'update':
                ticket_type_id = request.POST.get('ticket_type_id')
                try:
                    ticket_type = TicketType.objects.get(id=ticket_type_id, event=event)
                    form = TicketTypeForm(request.POST, instance=ticket_type)
                    if form.is_valid():
                        form.save()
                        messages.success(request, f'Ticket type "{ticket_type.name}" updated successfully.')
                        return redirect('ticket_types_management', event_slug=event.slug)
                    else:
                        messages.error(request, 'Please correct the errors below.')
                except TicketType.DoesNotExist:
                    messages.error(request, 'Ticket type not found.')
                    return redirect('ticket_types_management', event_slug=event.slug)
            
            elif action == 'delete':
                ticket_type_id = request.POST.get('ticket_type_id')
                try:
                    ticket_type = TicketType.objects.get(id=ticket_type_id, event=event)
                    ticket_type_name = ticket_type.name
                    ticket_type.delete()
                    messages.success(request, f'Ticket type "{ticket_type_name}" deleted successfully.')
                    return redirect('ticket_types_management', event_slug=event.slug)
                except TicketType.DoesNotExist:
                    messages.error(request, 'Ticket type not found.')
                    return redirect('ticket_types_management', event_slug=event.slug)
    
    # Get all ticket types for this event
    ticket_types = TicketType.objects.filter(event=event).order_by('cardinality')
    
    # Create empty form for new ticket type
    form = TicketTypeForm()
    
    # Get user's events for navigation
    user_events = Event.objects.filter(admins=request.user).order_by('-created_at')
    
    # Get user's ticket for this event
    my_ticket = None
    try:
        from tickets.models import NewTicket
        my_ticket = NewTicket.objects.filter(holder=request.user, event=event).first()
    except:
        pass
    
    context = {
        "event": event,
        "ticket_types": ticket_types,
        "form": form,
        "admin_events": user_events,
        "active_events": user_events,
        "nav_primary": "events",
        "nav_secondary": f"ticket_types_{event.slug}",
        "my_ticket": my_ticket.get_dto(user=request.user) if my_ticket else None,
        "now": timezone.now(),
    }
    
    return render(request, "mi_fuego/ticket_types_management.html", context)


@login_required
def ticket_types_ajax(request, event_slug):
    """AJAX endpoint for ticket type operations"""
    from django.http import JsonResponse
    from tickets.models import TicketType
    import json
    
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    # Get the event and check if user is admin
    try:
        event = Event.objects.get(slug=event_slug)
    except Event.DoesNotExist:
        return JsonResponse({'error': 'Event not found'}, status=404)
    
    # Check if user is admin of this event
    if not event.admins.filter(id=request.user.id).exists():
        return JsonResponse({'error': 'Permission denied'}, status=403)
    
    try:
        action = request.POST.get('action')
        
        if action == 'update_cardinality':
            # Handle drag and drop reordering
            ticket_type_ids = json.loads(request.POST.get('ticket_type_ids', '[]'))
            
            for index, ticket_type_id in enumerate(ticket_type_ids, 1):
                try:
                    ticket_type = TicketType.objects.get(id=ticket_type_id, event=event)
                    ticket_type.cardinality = index
                    ticket_type.save()
                except TicketType.DoesNotExist:
                    continue
            
            return JsonResponse({'success': True})
        
        elif action == 'update_field':
            # Handle individual field updates
            ticket_type_id = request.POST.get('ticket_type_id')
            field_name = request.POST.get('field_name')
            value = request.POST.get('value')
            
            if not ticket_type_id or not field_name:
                return JsonResponse({'error': 'Missing parameters'}, status=400)
            
            # Get the ticket type
            ticket_type = TicketType.objects.get(id=ticket_type_id, event=event)
            
            # Update the field
            if field_name in ['name', 'description']:
                setattr(ticket_type, field_name, value)
            elif field_name in ['price', 'ticket_count']:
                setattr(ticket_type, field_name, float(value) if value else None)
            elif field_name in ['date_from', 'date_to']:
                from django.utils.dateparse import parse_datetime
                if value:
                    parsed_date = parse_datetime(value)
                    setattr(ticket_type, field_name, parsed_date)
                else:
                    setattr(ticket_type, field_name, None)
            elif field_name == 'show_in_caja':
                setattr(ticket_type, field_name, value == 'true')
            
            ticket_type.save()
            
            return JsonResponse({
                'success': True,
                'ticket_type_id': ticket_type_id,
                'field_name': field_name,
                'value': value
            })
        
        else:
            return JsonResponse({'error': 'Invalid action'}, status=400)
    
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def caja_config_ajax(request, event_slug):
    """AJAX endpoint to update ticket type caja settings"""
    from django.http import JsonResponse
    from tickets.models import TicketType
    
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    # Get the event and check if user is admin
    try:
        event = Event.objects.get(slug=event_slug)
    except Event.DoesNotExist:
        return JsonResponse({'error': 'Event not found'}, status=404)
    
    # Check if user is admin of this event
    if not event.admins.filter(id=request.user.id).exists():
        return JsonResponse({'error': 'Permission denied'}, status=403)
    
    try:
        ticket_type_id = request.POST.get('ticket_type_id')
        field_name = request.POST.get('field_name')  # 'show_in_caja' or 'ignore_max_amount'
        value = request.POST.get('value') == 'true'  # Convert string to boolean
        
        if not ticket_type_id or not field_name:
            return JsonResponse({'error': 'Missing parameters'}, status=400)
        
        if field_name not in ['show_in_caja', 'ignore_max_amount']:
            return JsonResponse({'error': 'Invalid field name'}, status=400)
        
        # Get the ticket type
        ticket_type = TicketType.objects.get(id=ticket_type_id, event=event)
        
        # Update the field
        setattr(ticket_type, field_name, value)
        ticket_type.save()
        
        return JsonResponse({
            'success': True,
            'ticket_type_id': ticket_type_id,
            'field_name': field_name,
            'value': value
        })
        
    except TicketType.DoesNotExist:
        return JsonResponse({'error': 'Ticket type not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def scanner_events_view(request):
    """Show events where the user has scanner access"""
    # Get events where the user has scanner access (either as admin or explicit scanner access)
    admin_events = Event.objects.filter(admins=request.user)
    scanner_access_events = Event.objects.filter(access_scanner=request.user)
    
    # Combine both querysets and remove duplicates
    scanner_events = (admin_events | scanner_access_events).distinct().order_by('-is_main', 'name')
    
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
    
    context = {
        "scanner_events": scanner_events,
        "event": main_event,
        "active_events": user_events,
        "nav_primary": "scanner",
        "nav_secondary": "scanner_events",
        "my_ticket": my_ticket.get_dto(user=request.user) if my_ticket else None,
        "now": timezone.now(),
    }
    
    return render(request, "mi_fuego/scanner_events.html", context)


@login_required
def caja_events_view(request):
    """Show events where the user has caja access"""
    # Get events where the user has caja access (either as admin or explicit caja access)
    admin_events = Event.objects.filter(admins=request.user)
    caja_access_events = Event.objects.filter(access_caja=request.user)
    
    # Combine both querysets and remove duplicates
    caja_events = (admin_events | caja_access_events).distinct().order_by('-is_main', 'name')
    
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
    
    context = {
        "caja_events": caja_events,
        "event": main_event,
        "active_events": user_events,
        "nav_primary": "caja",
        "nav_secondary": "caja_events",
        "my_ticket": my_ticket.get_dto(user=request.user) if my_ticket else None,
        "now": timezone.now(),
    }
    
    return render(request, "mi_fuego/caja_events.html", context)


@login_required
def bonus_report_view(request, event_slug):
    """Show report of all sold bonuses for a specific event"""
    # Get the event and check if user is admin
    try:
        event = Event.objects.get(slug=event_slug)
    except Event.DoesNotExist:
        raise Http404("Event not found")
    
    # Check if user is admin of this event
    if not event.admins.filter(id=request.user.id).exists():
        return HttpResponseForbidden("You don't have permission to view this event")
    
    # Get all tickets sold for this event
    tickets = NewTicket.objects.filter(
        event=event
    ).select_related(
        'holder', 'owner', 'scanned_by', 'ticket_type', 'order'
    ).prefetch_related('ticket_photos').order_by('-created_at')
    
    # Get the main event for context
    main_event = Event.get_main_event()
    
    # Get events where the user is an admin
    admin_events = Event.objects.filter(
        admins=request.user
    ).order_by('-is_main', 'name')
    
    # Get events where user has tickets, prioritizing main event
    user_events = Event.get_active_events().filter(
        newticket__holder=request.user
    ).distinct().order_by('-is_main', 'name')
    
    # Get the first ticket for the main event (for backward compatibility)
    my_ticket = NewTicket.objects.filter(
        holder=request.user, event=main_event, owner=request.user
    ).first() if main_event else None
    
    # Prepare ticket data for the template
    tickets_data = []
    for ticket in tickets:
        # Determine if ticket is unassigned (has holder but no owner)
        # If attendee_must_be_registered is False, treat unassigned tickets as unused
        is_unassigned = ticket.holder and not ticket.owner and event.attendee_must_be_registered
        
        # Check if ticket was issued by caja (admin-generated order)
        is_caja_issued = ticket.order.generated_by_admin_user is not None
        
        # Get caja admin name (who issued the ticket)
        caja_admin_name = None
        if is_caja_issued:
            admin_user = ticket.order.generated_by_admin_user
            caja_admin_name = f"{admin_user.first_name} {admin_user.last_name}".strip()
            if not caja_admin_name or caja_admin_name == " ":
                caja_admin_name = admin_user.username
        
        ticket_data = {
            'key': ticket.key,
            'ticket_type': ticket.ticket_type.name,
            'ticket_type_emoji': ticket.ticket_type.emoji,
            'ticket_type_color': ticket.ticket_type.color,
            'holder_name': f"{ticket.holder.first_name} {ticket.holder.last_name}".strip() if ticket.holder else "Sin asignar",
            'holder_email': ticket.holder.email if ticket.holder else None,
            'owner_name': f"{ticket.owner.first_name} {ticket.owner.last_name}".strip() if ticket.owner else "Sin asignar",
            'owner_email': ticket.owner.email if ticket.owner else None,
            'is_used': ticket.is_used,
            'used_at': ticket.used_at,
            'scanned_by_name': f"{ticket.scanned_by.first_name} {ticket.scanned_by.last_name}".strip() if ticket.scanned_by else None,
            'scanned_by_email': ticket.scanned_by.email if ticket.scanned_by else None,
            'notes': ticket.notes,
            'is_unassigned': is_unassigned,
            'is_caja_issued': is_caja_issued,
            'caja_admin_name': caja_admin_name,
            'order_type': ticket.order.get_order_type_display() if ticket.order.order_type else None,
            'photos': [
                {
                    'id': photo.id,
                    'url': photo.photo.url,
                    'name': photo.photo.name.split('/')[-1],
                    'uploaded_at': photo.created_at,
                    'uploaded_by': f"{photo.uploaded_by.first_name} {photo.uploaded_by.last_name}".strip() if photo.uploaded_by else None
                }
                for photo in ticket.ticket_photos.all()
            ],
            'created_at': ticket.created_at,
            'order_key': ticket.order.key if ticket.order else None,
        }
        tickets_data.append(ticket_data)
    
    # Calculate statistics
    total_tickets = len(tickets_data)
    used_tickets = len([t for t in tickets_data if t['is_used']])
    unassigned_tickets = len([t for t in tickets_data if t['is_unassigned']])
    unused_tickets = total_tickets - used_tickets - unassigned_tickets
    caja_issued_tickets = len([t for t in tickets_data if t['is_caja_issued']])
    
    context = {
        "event": event,
        "main_event": main_event,
        "admin_events": admin_events,
        "active_events": user_events,
        "nav_primary": "events",
        "nav_secondary": f"bonus_report_{event.slug}",
        "my_ticket": my_ticket.get_dto(user=request.user) if my_ticket else None,
        "now": timezone.now(),
        "tickets": tickets_data,
        "stats": {
            "total_tickets": total_tickets,
            "used_tickets": used_tickets,
            "unused_tickets": unused_tickets,
            "unassigned_tickets": unassigned_tickets,
            "caja_issued_tickets": caja_issued_tickets,
            "usage_percentage": (used_tickets / total_tickets * 100) if total_tickets > 0 else 0,
        }
    }
    
    return render(request, "mi_fuego/bonus_report.html", context)


@login_required
def my_tickets_ajax(request, event_slug=None):
    """AJAX endpoint to get updated ticket status for auto-refresh"""
    from django.utils import timezone
    
    # If event_slug is provided, get tickets for that specific event
    if event_slug:
        # Special case: if slug is "eventos-anteriores", return empty for now
        if event_slug == "eventos-anteriores":
            return JsonResponse({"tickets": []})
        
        try:
            current_event = Event.get_by_slug(event_slug)
            if not current_event:
                return JsonResponse({"error": "Event not found"}, status=404)
                
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
            
            # Get the last update time from request to detect changes
            last_update = request.GET.get('last_update')
            last_update_time = None
            if last_update:
                try:
                    from django.utils.dateparse import parse_datetime
                    last_update_time = parse_datetime(last_update)
                except:
                    last_update_time = None
            
            # Organize tickets for this event
            tickets_dto = []
            for ticket in all_tickets:
                ticket_dto = ticket.get_dto(user=request.user)
                
                # Add initial state information
                ticket_dto['is_used'] = ticket.is_used
                ticket_dto['used_at'] = ticket.used_at.isoformat() if ticket.used_at else None
                    
                tickets_dto.append(ticket_dto)
            
            return JsonResponse({
                "tickets": tickets_dto,
                "event": {
                    "name": current_event.name,
                    "slug": current_event.slug,
                    "transfers_enabled_until": current_event.transfers_enabled_until.isoformat() if current_event.transfers_enabled_until else None,
                    "attendee_must_be_registered": current_event.attendee_must_be_registered,
                },
                "now": timezone.now().isoformat(),
                "is_volunteer": my_ticket.is_volunteer() if my_ticket else False,
            })
            
        except Event.DoesNotExist:
            return JsonResponse({"error": "Event not found"}, status=404)
    
    # If no event_slug, return error
    return JsonResponse({"error": "Event slug required"}, status=400)


@login_required
def caja_view(request, event_slug):
    """Vista para la página de caja del administrador de eventos"""
    from django.utils import timezone
    from django.contrib import messages
    from django.db import transaction
    from .forms import CajaEmitirBonoForm
    from tickets.models import TicketType, NewTicket, Order
    from events.models import Event
    from django.contrib.auth import get_user_model
    
    User = get_user_model()
    
    # Get the event and check if user is admin
    try:
        event = Event.objects.get(slug=event_slug)
    except Event.DoesNotExist:
        raise Http404("Event not found")
    
    # Check if user is admin or has caja access for this event
    has_caja_access = (event.admins.filter(id=request.user.id).exists() or 
                      event.access_caja.filter(id=request.user.id).exists())
    if not has_caja_access:
        return HttpResponseForbidden("You don't have permission to access caja for this event")
    
    # Initialize form
    form = CajaEmitirBonoForm(event)
    
    # Handle form submission
    if request.method == 'POST':
        print(f"DEBUG POST: Formulario recibido")
        print(f"DEBUG POST: Datos POST: {request.POST}")
        form = CajaEmitirBonoForm(event, request.POST)
        print(f"DEBUG POST: Formulario válido: {form.is_valid()}")
        if not form.is_valid():
            print(f"DEBUG POST: Errores del formulario: {form.errors}")
            print(f"DEBUG POST: Datos del formulario: {form.cleaned_data}")
            # Si el formulario no es válido, renderizar con errores
            context = {
                "event": event,
                "main_event": Event.get_main_event(),
                "admin_events": Event.objects.filter(admins=request.user).order_by('-is_main', 'name'),
                "active_events": Event.get_active_events().filter(newticket__holder=request.user).distinct().order_by('-is_main', 'name'),
                "nav_primary": "events",
                "nav_secondary": f"caja_{event.slug}",
                "form": form,
            }
            return render(request, "mi_fuego/caja.html", context)
        if form.is_valid():
            try:
                with transaction.atomic():
                    # Handle multiple ticket types from the new interface
                    ticket_types = request.POST.getlist('ticket_types')
                    quantities = request.POST.getlist('quantities')
                    print(f"DEBUG POST: ticket_types: {ticket_types}")
                    print(f"DEBUG POST: quantities: {quantities}")
                    
                    # Fallback to single ticket type for backward compatibility
                    if not ticket_types:
                        print("DEBUG POST: No hay ticket_types, usando fallback")
                        ticket_type = form.cleaned_data.get('ticket_type')
                        quantity = form.cleaned_data.get('quantity')
                        if not ticket_type or not quantity:
                            messages.error(request, 'Debe seleccionar al menos un tipo de bono y cantidad.')
                            return redirect('caja', event_slug=event.slug)
                        ticket_types = [ticket_type.id]
                        quantities = [quantity]
                        print(f"DEBUG POST: Fallback - ticket_types: {ticket_types}, quantities: {quantities}")
                    
                    # Validar que hay datos
                    if not ticket_types or not quantities:
                        messages.error(request, 'Debe seleccionar al menos un tipo de bono y cantidad.')
                        return redirect('caja', event_slug=event.slug)
                    
                    payment_method = form.cleaned_data['payment_method']
                    email = form.cleaned_data.get('email')
                    mark_as_used = form.cleaned_data.get('mark_as_used', False)
                    
                    # Validar que si no está marcado como usado, debe tener email
                    if not mark_as_used and not email:
                        messages.error(request, 'Debe proporcionar un email o marcar como usado (venta en puerta).')
                        return redirect('caja', event_slug=event.slug)
                    
                    # Create or get user if email provided
                    user = None
                    if email:
                        try:
                            user = User.objects.get(email=email)
                        except User.DoesNotExist:
                            # Create new user account
                            import uuid
                            from allauth.account.models import EmailAddress
                            
                            user = User.objects.create_user(
                                username=str(uuid.uuid4()),
                                email=email.lower(),
                                first_name='',
                                last_name='',
                            )
                            
                            # Create profile for the user
                            if not hasattr(user, 'profile'):
                                from .models import Profile
                                Profile.objects.create(user=user)
                            
                            # Set profile completion as NONE so user completes it after password reset
                            user.profile.profile_completion = 'NONE'
                            user.profile.save()
                            
                            # Create verified email address
                            EmailAddress.objects.create(
                                user=user,
                                email=email.lower(),
                                verified=True,
                                primary=True
                            )
                            
                            # Send password reset email
                            from allauth.account.forms import ResetPasswordForm
                            reset_form = ResetPasswordForm(data={'email': user.email.lower()})
                            if reset_form.is_valid():
                                reset_form.save(
                                    subject_template_name='account/email/password_reset_key_subject.txt',
                                    email_template_name='account/email/password_reset_key_message.html',
                                    from_email=settings.DEFAULT_FROM_EMAIL,
                                    request=request,
                                    use_https=False,
                                    html_email_template_name=None,
                                    extra_email_context=None
                                )
                            
                            messages.success(request, f'Usuario creado automáticamente: {email}')
                    
                    # Map payment method to order type
                    payment_method_mapping = {
                        'efectivo': Order.OrderType.CASH_ONSITE,
                        'transferencia': Order.OrderType.LOCAL_TRANSFER,
                        'transferencia_internacional': Order.OrderType.INTERNATIONAL_TRANSFER,
                    }
                    
                    # Calculate total amount and validate limits
                    total_amount = 0
                    ticket_data = []
                    validation_errors = []
                    
                    # Get current ticket counts for the event (only from ticket types that don't ignore max amount)
                    from django.db.models import Count
                    current_tickets_sold = NewTicket.objects.filter(
                        event=event,
                        order__status=Order.OrderStatus.CONFIRMED,
                        ticket_type__ignore_max_amount=False
                    ).count()
                    
                    for i, ticket_type_id in enumerate(ticket_types):
                        try:
                            ticket_type = TicketType.objects.get(id=ticket_type_id, event=event)
                            quantity = int(quantities[i])
                            
                            if quantity <= 0:
                                continue
                            
                            # Validate ticket type limit - check if there are enough tickets available
                            # The ticket_type.ticket_count represents the remaining tickets after each sale
                            if ticket_type.ticket_count < quantity:
                                validation_errors.append(
                                    f'No hay suficientes bonos disponibles para "{ticket_type.name}". '
                                    f'Solicitados: {quantity}, Disponibles: {ticket_type.ticket_count}'
                                )
                                continue
                            
                            # Validate event max tickets limit (only if ticket type doesn't ignore max amount)
                            if not ticket_type.ignore_max_amount and event.max_tickets:
                                if current_tickets_sold + quantity > event.max_tickets:
                                    validation_errors.append(
                                        f'Se excede el límite máximo de bonos del evento ({event.max_tickets}). '
                                        f'Bonos ya vendidos: {current_tickets_sold}, Solicitados: {quantity}'
                                    )
                                    continue
                            
                            total_amount += ticket_type.price * quantity
                            ticket_data.append({
                                'ticket_type': ticket_type,
                                'quantity': quantity
                            })
                            
                        except (TicketType.DoesNotExist, ValueError, IndexError):
                            continue
                    
                    # Check for validation errors
                    if validation_errors:
                        for error in validation_errors:
                            messages.error(request, error)
                        return redirect('caja', event_slug=event.slug)
                    
                    if not ticket_data:
                        messages.error(request, 'No se encontraron tipos de bono válidos.')
                        return render(request, 'mi_fuego/caja.html', {'form': form, 'event': event})
                    
                    # Create order for the tickets
                    order = Order.objects.create(
                        event=event,
                        status=Order.OrderStatus.CONFIRMED,
                        amount=total_amount,
                        net_received_amount=total_amount,  # For caja orders, amount == net_received_amount (no MP fees)
                        order_type=payment_method_mapping.get(payment_method, Order.OrderType.OTHER),
                        first_name=user.first_name if user else 'Caja',
                        last_name=user.last_name if user else 'Admin',
                        email=user.email if user else 'caja@admin.com',
                        phone=user.profile.phone if user and hasattr(user, 'profile') else '',
                        dni=user.profile.document_number if user and hasattr(user, 'profile') else '',
                        generated_by_admin_user=request.user
                    )
                    
                    # Check if user already has a ticket for this event
                    user_already_has_ticket = False
                    if user:
                        user_already_has_ticket = NewTicket.objects.filter(owner=user, event=event).exists()
                    
                    # Create tickets for each type
                    tickets_created = []
                    for data in ticket_data:
                        ticket_type = data['ticket_type']
                        quantity = data['quantity']
                        
                        for i in range(quantity):
                            # Set owner only if user doesn't already have a ticket for this event
                            ticket_owner = None
                            if user and not user_already_has_ticket:
                                ticket_owner = user
                                user_already_has_ticket = True  # Mark that user now has a ticket
                            
                            ticket = NewTicket(
                                event=event,
                                ticket_type=ticket_type,
                                order=order,
                                holder=user if user else None,
                                owner=ticket_owner,
                                is_used=mark_as_used,
                                used_at=timezone.now() if mark_as_used else None,  # Timestamp cuando se marca como usado
                                scanned_by=request.user if mark_as_used else None  # Admin que emite el bono como escáner
                            )
                            ticket.save()  # Use the custom save method without parameters
                            tickets_created.append(ticket)
                    
                    # Send email with bonus information to the user if they exist and tickets were created
                    if user and tickets_created and not mark_as_used:
                        from utils.email import send_mail
                        try:
                            send_mail(
                                template_name='bonus_issued',
                                recipient_list=[user.email],
                                context={
                                    'user': user,
                                    'event': event,
                                    'tickets': tickets_created,
                                    'order': order,
                                    'total_amount': total_amount,
                                }
                            )
                            messages.success(request, f'Email enviado a {user.email} con información del bono')
                        except Exception as e:
                            messages.warning(request, f'Bono creado pero error al enviar email: {str(e)}')
                    
                    # Success message
                    total_tickets = len(tickets_created)
                    total_amount_formatted = f"${total_amount:,.0f}".replace(',', '.')
                    
                    success_message = ""
                    if user:
                        if mark_as_used:
                            success_message = f'Orden #{order.id} generada correctamente. Se emitieron {total_tickets} bonos por un total de {total_amount_formatted} para {user.email} (marcados como usados - venta en puerta)'
                        else:
                            success_message = f'Orden #{order.id} generada correctamente. Se emitieron {total_tickets} bonos por un total de {total_amount_formatted} para {user.email}'
                    else:
                        if mark_as_used:
                            success_message = f'Orden #{order.id} generada correctamente. Se emitieron {total_tickets} bonos por un total de {total_amount_formatted} (marcados como usados - venta en puerta)'
                        else:
                            success_message = f'Orden #{order.id} generada correctamente. Se emitieron {total_tickets} bonos por un total de {total_amount_formatted} (sin usuario asignado)'
                    
                    print(f"DEBUG: Creando mensaje: {success_message}")
                    messages.success(request, success_message)
                    print(f"DEBUG: Mensaje creado. Total mensajes: {len(messages.get_messages(request))}")
                    
                    # Redirect to prevent form resubmission on F5
                    return redirect('caja', event_slug=event.slug)
                    
            except Exception as e:
                messages.error(request, f'Error al emitir bonos: {str(e)}')
    
    # Get the main event for context
    main_event = Event.get_main_event()
    
    # Get events where the user is an admin
    admin_events = Event.objects.filter(
        admins=request.user
    ).order_by('-is_main', 'name')
    
    # Get events where user has tickets, prioritizing main event
    user_events = Event.get_active_events().filter(
        newticket__holder=request.user
    ).distinct().order_by('-is_main', 'name')
    
    # Get the first ticket for the main event (for backward compatibility)
    my_ticket = NewTicket.objects.filter(
        holder=request.user, event=main_event, owner=request.user
    ).first() if main_event else None
    
    # Get statistics for the caja page (same as event_admin_view)
    from django.db import connection
    from decimal import Decimal
    
    with connection.cursor() as cursor:
        query = """
            SELECT 
                -- Total tickets sold (from newticket table, including ALL tickets for accurate statistics)
                COUNT(DISTINCT nt.id) as tickets_sold,
                COALESCE(SUM(CASE WHEN nt.is_used = true THEN 1 ELSE 0 END), 0) as tickets_used,
                COUNT(DISTINCT too.id) as total_orders,
                COALESCE(SUM(too.amount), 0) as total_revenue,
                COALESCE(SUM(too.net_received_amount), 0) as net_received_amount,
                -- Donations
                COALESCE(SUM(too.donation_art), 0) as donations_art,
                COALESCE(SUM(too.donation_venue), 0) as donations_venue,
                COALESCE(SUM(too.donation_grant), 0) as donations_grant,
                COALESCE(SUM(too.donation_art + too.donation_venue + too.donation_grant), 0) as total_donations,
                -- Caja orders (generated_by_admin_user_id is not null)
                COALESCE(SUM(CASE WHEN too.generated_by_admin_user_id IS NOT NULL THEN COALESCE(tot.quantity, 0) ELSE 0 END), 0) as caja_tickets_sold,
                COALESCE(SUM(CASE WHEN too.generated_by_admin_user_id IS NOT NULL THEN (too.amount - COALESCE(too.donation_art, 0) - COALESCE(too.donation_venue, 0) - COALESCE(too.donation_grant, 0)) ELSE 0 END), 0) as caja_ticket_revenue,
                COALESCE(SUM(CASE WHEN too.generated_by_admin_user_id IS NOT NULL THEN too.amount ELSE 0 END), 0) as caja_total_revenue,
                COALESCE(SUM(CASE WHEN too.generated_by_admin_user_id IS NOT NULL THEN too.net_received_amount ELSE 0 END), 0) as caja_net_received_amount,
                COUNT(DISTINCT CASE WHEN too.generated_by_admin_user_id IS NOT NULL THEN too.id END) as caja_total_orders,
                -- Regular orders (generated_by_admin_user_id is null) - only from orderticket table
                COUNT(DISTINCT CASE WHEN too.generated_by_admin_user_id IS NULL THEN nt.id END) as regular_tickets_sold,
                COALESCE(SUM(CASE WHEN too.generated_by_admin_user_id IS NULL THEN (too.amount - COALESCE(too.donation_art, 0) - COALESCE(too.donation_venue, 0) - COALESCE(too.donation_grant, 0)) ELSE 0 END), 0) as regular_ticket_revenue,
                COALESCE(SUM(CASE WHEN too.generated_by_admin_user_id IS NULL THEN too.amount ELSE 0 END), 0) as regular_total_revenue,
                COALESCE(SUM(CASE WHEN too.generated_by_admin_user_id IS NULL THEN too.net_received_amount ELSE 0 END), 0) as regular_net_received_amount,
                COUNT(DISTINCT CASE WHEN too.generated_by_admin_user_id IS NULL THEN too.id END) as regular_total_orders
            FROM tickets_order too
            LEFT JOIN tickets_orderticket tot ON too.id = tot.order_id
            LEFT JOIN tickets_newticket nt ON too.id = nt.order_id
            LEFT JOIN tickets_tickettype tt ON nt.ticket_type_id = tt.id
            WHERE too.event_id = %s AND too.status = 'CONFIRMED'
        """
        cursor.execute(query, [event.id])
        result = cursor.fetchone()
    
    # Calculate percentage sold
    percentage_sold = 0
    if event.max_tickets and event.max_tickets > 0:
        total_tickets_sold = result[0] or 0  # tickets_sold (already includes both regular and caja)
        percentage_sold = (total_tickets_sold / event.max_tickets) * 100
    
    # Calculate proportional distribution of commissions
    # Only calculate commissions for regular orders (exclude caja orders)
    regular_total_revenue = result[15] or Decimal('0')  # regular_total_revenue
    regular_net_received_amount = result[16] or Decimal('0')  # regular_net_received_amount
    regular_commissions = regular_total_revenue - regular_net_received_amount
    
    # Calculate commission rate
    commission_rate = Decimal('0')
    if regular_total_revenue > 0:
        commission_rate = regular_commissions / regular_total_revenue
    
    # Calculate net amounts for donations (donations are net amounts, no commissions)
    donations_art_net = result[5] or Decimal('0')  # donations_art
    donations_venue_net = result[6] or Decimal('0')  # donations_venue  
    donations_grant_net = result[7] or Decimal('0')  # donations_grant
    
    # Debug messages
    all_messages = list(messages.get_messages(request))
    print(f"DEBUG GET: Total mensajes en request: {len(all_messages)}")
    for i, msg in enumerate(all_messages):
        print(f"DEBUG GET: Mensaje {i}: {msg.tags} - {msg}")
    
    context = {
        "event": event,
        "main_event": main_event,
        "admin_events": admin_events,
        "active_events": user_events,
        "nav_primary": "events",
        "nav_secondary": f"caja_{event.slug}",
        "my_ticket": my_ticket.get_dto(user=request.user) if my_ticket else None,
        "now": timezone.now(),
        "form": form,
        "event_limits": {
            "max_tickets": event.max_tickets,
            "tickets_remaining": event.tickets_remaining() if event.max_tickets else None,
        },
        "stats": {
            "tickets_sold": result[0] or 0,
            "tickets_used": result[1] or 0,
            "total_orders": result[2] or 0,
            "total_revenue": result[3] or Decimal('0'),
            "net_received_amount": result[4] or Decimal('0'),
            "donations_art": result[5] or Decimal('0'),
            "donations_venue": result[6] or Decimal('0'),
            "donations_grant": result[7] or Decimal('0'),
            "total_donations": result[8] or Decimal('0'),
            # Venue occupancy statistics
            "venue_occupancy": event.venue_occupancy,
            "venue_capacity": event.venue_capacity,
            "occupancy_percentage": event.occupancy_percentage,
            "attendees_left": event.attendees_left,
            "caja_tickets_sold": result[9] or 0,
            "caja_ticket_revenue": result[10] or Decimal('0'),
            "caja_total_revenue": result[11] or Decimal('0'),
            "caja_net_received_amount": result[12] or Decimal('0'),
            "caja_total_orders": result[13] or 0,
            "regular_tickets_sold": result[14] or 0,
            "regular_ticket_revenue": result[15] or Decimal('0'),
            "regular_total_revenue": result[16] or Decimal('0'),
            "regular_net_received_amount": result[17] or Decimal('0'),
            "regular_total_orders": result[18] or 0,
            "percentage_sold": percentage_sold,
            "commission_rate": commission_rate,
            "commission_percentage": (commission_rate * 100) if regular_total_revenue > 0 else 0,
            "donations_art_net": donations_art_net,
            "donations_venue_net": donations_venue_net,
            "donations_grant_net": donations_grant_net,
        },
    }
    
    return render(request, "mi_fuego/caja.html", context)
