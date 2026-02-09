from django.contrib.auth.decorators import user_passes_test, login_required
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponseForbidden
from django.utils import timezone
from tickets.models import NewTicket
from events.models import Event, GrupoMiembro
from django.core.exceptions import ObjectDoesNotExist
from user_profile.models import Profile

def is_admin_or_puerta(user):
    return user.is_superuser or user.groups.filter(name='Puerta').exists()

def has_scanner_access(user, event=None):
    """Check if user has scanner access for an event"""
    if user.is_superuser:
        return True
    if event:
        # Check if user is an event admin (admins should have scanner access)
        if event.admins.filter(id=user.id).exists():
            return True
        # Check if user has explicit scanner access
        if event.access_scanner.filter(id=user.id).exists():
            return True
    # Fallback to old group-based logic for backward compatibility
    return user.groups.filter(name='Puerta').exists()


def _group_info_for_ticket(ticket):
    """Return early access and group info for the ticket's holder (or owner)."""
    user = ticket.holder or ticket.owner
    if not user:
        return None
    gm = GrupoMiembro.objects.filter(
        grupo__event=ticket.event,
        user=user,
    ).select_related('grupo', 'grupo__tipo').first()
    if not gm:
        return None
    has_early = gm.ingreso_anticipado or bool(gm.ingreso_anticipado_fecha)
    grupo_label = gm.grupo.nombre
    if getattr(gm.grupo, 'tipo', None) and gm.grupo.tipo:
        grupo_label = f"{gm.grupo.tipo.nombre} - {gm.grupo.nombre}"
    # Fecha desde la cual tiene ingreso anticipado (hora tal como en la DB, sin convertir timezone)
    ingreso_desde = None
    if gm.ingreso_anticipado_fecha:
        ingreso_desde = gm.ingreso_anticipado_fecha.strftime("%d/%m/%Y")
    elif gm.grupo.ingreso_anticipado_desde:
        ingreso_desde = gm.grupo.ingreso_anticipado_desde.strftime("%d/%m/%Y %H:%M")
    return {
        'has_early_access': has_early,
        'grupo_nombre': grupo_label,
        'ingreso_anticipado_desde': ingreso_desde,
        'late_checkout': gm.late_checkout,
    }


def _ticket_check_response(ticket):
    """Build the JSON response dict for check_ticket / check_ticket_by_dni."""
    profile = getattr(ticket.holder, 'profile', None) if ticket.holder else None
    user_info = None
    if ticket.holder:
        user_info = {
            'first_name': ticket.holder.first_name,
            'last_name': ticket.holder.last_name,
            'document_type': profile.document_type if profile else None,
            'document_number': profile.document_number if profile else None,
        }
    out = {
        'key': ticket.key,
        'ticket_type': str(ticket.ticket_type),
        'is_used': ticket.is_used,
        'holder_left': getattr(ticket, 'holder_left', False),
        'used_at': ticket.used_at.isoformat() if ticket.used_at else None,
        'scanned_by': {
            'id': ticket.scanned_by.id,
            'username': ticket.scanned_by.username,
            'full_name': ticket.scanned_by.get_full_name() or ticket.scanned_by.username,
            'email': ticket.scanned_by.email,
        } if ticket.scanned_by else None,
        'notes': ticket.notes,
        'photos': [
            {
                'id': photo.id,
                'url': photo.photo.url,
                'name': photo.photo.name.split('/')[-1],
                'uploaded_at': photo.created_at.isoformat() if photo.created_at else None,
                'uploaded_by': photo.uploaded_by.get_full_name() or photo.uploaded_by.username,
            }
            for photo in ticket.ticket_photos.all()
        ],
        'owner_name': f"{ticket.owner.first_name} {ticket.owner.last_name}" if ticket.owner else None,
        'user_info': user_info,
    }
    group_info = _group_info_for_ticket(ticket)
    if group_info:
        out['group_info'] = group_info
    else:
        out['group_info'] = None
    return out


@user_passes_test(is_admin_or_puerta)
def scan_tickets(request):
    return render(request, 'mi_fuego/admin/scan_tickets.html')


@login_required
def scanner_dashboard(request, event_slug):
    """Dashboard de entradas/salidas para puerta y admin: totales e histogramas por tiempo."""
    import json
    from collections import defaultdict
    from datetime import datetime, timedelta

    event = get_object_or_404(Event, slug=event_slug)
    if not has_scanner_access(request.user, event):
        return HttpResponseForbidden("No tienes permisos para acceder al dashboard de este evento")

    # Tickets que entraron (is_used) y que salieron (holder_left); left_at puede ser None si es registro viejo
    used_tickets = NewTicket.objects.filter(event=event, is_used=True).values_list('used_at', flat=True)
    used_at_list = [t for t in used_tickets if t is not None]
    left_tickets = NewTicket.objects.filter(event=event, holder_left=True).values_list('left_at', flat=True)
    left_at_list = [t for t in left_tickets if t is not None]

    total_entraron = len(used_at_list)
    total_salieron = len(left_at_list)

    # Timeframe: desde el primero hasta el último (entrada o salida)
    all_times = used_at_list + left_at_list
    if not all_times:
        time_min = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        time_max = timezone.now()
        histogram_entries = []
        histogram_exits = []
    else:
        time_min = timezone.localtime(min(all_times)).replace(minute=0, second=0, microsecond=0)
        time_max_utc = max(all_times)
        time_max = timezone.localtime(time_max_utc)
        if time_max.minute or time_max.second or time_max.microsecond:
            time_max = time_max.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            time_max = time_max.replace(minute=0, second=0, microsecond=0)

        buckets_entries = defaultdict(int)
        buckets_exits = defaultdict(int)
        current = time_min
        while current <= time_max:
            key = current.strftime("%d/%m %H:%M")
            buckets_entries[key] = 0
            buckets_exits[key] = 0
            current += timedelta(hours=1)

        for utc_dt in used_at_list:
            local_dt = timezone.localtime(utc_dt)
            bucket = local_dt.replace(minute=0, second=0, microsecond=0)
            key = bucket.strftime("%d/%m %H:%M")
            buckets_entries[key] = buckets_entries.get(key, 0) + 1
        for utc_dt in left_at_list:
            local_dt = timezone.localtime(utc_dt)
            bucket = local_dt.replace(minute=0, second=0, microsecond=0)
            key = bucket.strftime("%d/%m %H:%M")
            buckets_exits[key] = buckets_exits.get(key, 0) + 1

        labels = sorted(buckets_entries.keys(), key=lambda x: datetime.strptime(x, "%d/%m %H:%M"))
        histogram_entries = [{"label": lb, "count": buckets_entries[lb]} for lb in labels]
        histogram_exits = [{"label": lb, "count": buckets_exits[lb]} for lb in labels]

    context = {
        "event": event,
        "total_entraron": total_entraron,
        "total_salieron": total_salieron,
        "time_min": time_min,
        "time_max": time_max,
        "histogram_entries_json": json.dumps(histogram_entries),
        "histogram_exits_json": json.dumps(histogram_exits),
    }
    return render(request, "mi_fuego/admin/scanner_dashboard.html", context)


@login_required
def scan_tickets_event(request, event_slug):
    """Scanner for a specific event with new access control"""
    from django.db import connection
    from decimal import Decimal
    
    event = get_object_or_404(Event, slug=event_slug)
    
    # Check if user has scanner access for this event
    if not has_scanner_access(request.user, event):
        return HttpResponseForbidden("No tienes permisos para acceder al scanner de este evento")
    
    # Get statistics for the scanner
    with connection.cursor() as cursor:
        query = """
            SELECT 
                -- Total tickets sold (from newticket table, including ALL tickets for scanner display)
                COUNT(DISTINCT nt.id) as tickets_sold,
                COALESCE(SUM(CASE WHEN nt.is_used = true THEN 1 ELSE 0 END), 0) as tickets_used,
                -- Caja orders (generated_by_admin_user_id is not null)
                COUNT(DISTINCT CASE WHEN too.generated_by_admin_user_id IS NOT NULL THEN nt.id END) as caja_tickets_sold,
                -- Regular orders (generated_by_admin_user_id is null) - only from newticket table
                COUNT(DISTINCT CASE WHEN too.generated_by_admin_user_id IS NULL THEN nt.id END) as regular_tickets_sold
            FROM tickets_order too
            LEFT JOIN tickets_orderticket tot ON too.id = tot.order_id
            LEFT JOIN tickets_newticket nt ON too.id = nt.order_id
            LEFT JOIN tickets_tickettype tt ON nt.ticket_type_id = tt.id
            WHERE too.event_id = %s AND too.status = 'CONFIRMED'
        """
        cursor.execute(query, [event.id])
        result = cursor.fetchone()
    
    # Calculate percentage used
    percentage_used = 0
    total_tickets = result[0] or 0  # tickets_sold (already includes both regular and caja)
    tickets_used = result[1] or 0
    if total_tickets > 0:
        percentage_used = (tickets_used / total_tickets) * 100
    
    context = {
        'event': event,
        'stats': {
            'tickets_sold': result[0] or 0,
            'tickets_used': tickets_used,
            'total_tickets': total_tickets,
            'percentage_used': percentage_used,
        },
    }
    return render(request, 'mi_fuego/admin/scan_tickets.html', context)

@login_required
def event_stats_api(request, event_slug):
    """API endpoint to get real-time event statistics for the scanner"""
    from django.db import connection
    from django.http import JsonResponse
    
    event = get_object_or_404(Event, slug=event_slug)
    
    # Check if user has scanner access for this event
    if not has_scanner_access(request.user, event):
        return JsonResponse({"error": "No tienes permisos para acceder a este evento"}, status=403)
    
    # Get statistics for the event
    with connection.cursor() as cursor:
        query = """
            SELECT 
                -- Total tickets sold (from newticket table, including ALL tickets for scanner display)
                COUNT(DISTINCT nt.id) as tickets_sold,
                COALESCE(SUM(CASE WHEN nt.is_used = true THEN 1 ELSE 0 END), 0) as tickets_used,
                -- Caja orders (generated_by_admin_user_id is not null)
                COUNT(DISTINCT CASE WHEN too.generated_by_admin_user_id IS NOT NULL THEN nt.id END) as caja_tickets_sold,
                -- Regular orders (generated_by_admin_user_id is null) - only from newticket table
                COUNT(DISTINCT CASE WHEN too.generated_by_admin_user_id IS NULL THEN nt.id END) as regular_tickets_sold
            FROM tickets_order too
            LEFT JOIN tickets_orderticket tot ON too.id = tot.order_id
            LEFT JOIN tickets_newticket nt ON too.id = nt.order_id
            LEFT JOIN tickets_tickettype tt ON nt.ticket_type_id = tt.id
            WHERE too.event_id = %s AND too.status = 'CONFIRMED'
        """
        cursor.execute(query, [event.id])
        result = cursor.fetchone()
    
    # Calculate percentage used
    percentage_used = 0
    total_tickets = result[0] or 0  # tickets_sold (already includes both regular and caja)
    tickets_used = result[1] or 0
    if total_tickets > 0:
        percentage_used = (tickets_used / total_tickets) * 100
    
    return JsonResponse({
        'tickets_sold': result[0] or 0,
        'tickets_used': tickets_used,
        'total_tickets': total_tickets,
        'percentage_used': percentage_used,
        'venue_capacity': event.venue_capacity,
        'venue_occupancy': event.venue_occupancy,
        'attendees_left': event.attendees_left,
        'occupancy_percentage': event.occupancy_percentage,
    })

def check_ticket_public(request, ticket_key):
    """
    Public endpoint to check only if a ticket is used (for polling on public ticket page)
    """
    try:
        ticket = NewTicket.objects.get(key=ticket_key)
        return JsonResponse({
            'is_used': ticket.is_used,
        })
    except NewTicket.DoesNotExist:
        return JsonResponse({'error': 'Ticket no encontrado'}, status=404)

def check_ticket(request, ticket_key):
    try:
        ticket = NewTicket.objects.get(key=ticket_key)
        
        # Get the current event from the request (passed from the scanner page)
        current_event_slug = request.GET.get('event_slug')
        if current_event_slug:
            try:
                current_event = Event.objects.get(slug=current_event_slug)
                # Check if the ticket belongs to the current event
                if ticket.event != current_event:
                    return JsonResponse({'error': f'Este bono pertenece al evento "{ticket.event.name}" pero estás escaneando para el evento "{current_event.name}"'}, status=400)
            except Event.DoesNotExist:
                return JsonResponse({'error': 'Evento no encontrado'}, status=404)
        
        # Check if user has scanner access for this ticket's event
        if not has_scanner_access(request.user, ticket.event):
            return JsonResponse({'error': 'No tienes permisos para verificar tickets de este evento'}, status=403)
        
        return JsonResponse(_ticket_check_response(ticket))
    except ObjectDoesNotExist:
        return JsonResponse({'error': 'Bono no encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def check_ticket_by_dni(request):
    """Look up ticket(s) by DNI or last name for the current event. Returns single ticket or list of results."""
    event_slug = request.GET.get('event_slug')
    q = (request.GET.get('q') or request.GET.get('dni') or '').strip()
    if not event_slug:
        return JsonResponse({'error': 'Falta el evento (event_slug)'}, status=400)
    if not q:
        return JsonResponse({'error': 'Ingresá DNI o apellido para buscar'}, status=400)
    try:
        event = Event.objects.get(slug=event_slug)
        if not has_scanner_access(request.user, event):
            return JsonResponse({'error': 'No tienes permisos para verificar tickets de este evento'}, status=403)

        # 1) Try exact DNI match first
        profile = Profile.objects.filter(document_number__iexact=q).first()
        if profile:
            ticket = NewTicket.objects.filter(event=event, holder=profile.user).order_by('id').first()
            if ticket:
                return JsonResponse(_ticket_check_response(ticket))
            return JsonResponse({'error': 'No se encontró ningún bono con ese DNI en este evento'}, status=404)

        # 2) Search by last name (holder)
        tickets = (
            NewTicket.objects.filter(event=event, holder__last_name__icontains=q)
            .select_related('holder', 'ticket_type')
            .order_by('holder__last_name', 'holder__first_name')
        )
        if not tickets.exists():
            return JsonResponse({'error': 'No se encontró ningún bono con ese DNI o apellido en este evento'}, status=404)
        if tickets.count() == 1:
            return JsonResponse(_ticket_check_response(tickets[0]))

        # 3) Multiple matches: return list for user to choose
        def _holder_doc(t):
            if not t.holder:
                return ''
            p = getattr(t.holder, 'profile', None)
            return p.document_number if p else ''
        results = []
        for t in tickets:
            gi = _group_info_for_ticket(t)
            results.append({
                'key': str(t.key),
                'ticket_type': str(t.ticket_type),
                'holder_name': f'{t.holder.first_name or ""} {t.holder.last_name or ""}'.strip() if t.holder else '',
                'document_number': _holder_doc(t),
                'is_used': t.is_used,
                'has_early_access': gi['has_early_access'] if gi else False,
                'grupo_nombre': gi['grupo_nombre'] if gi else None,
                'ingreso_anticipado_desde': gi.get('ingreso_anticipado_desde') if gi else None,
            })
        return JsonResponse({'results': results})
    except Event.DoesNotExist:
        return JsonResponse({'error': 'Evento no encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def mark_ticket_used(request, ticket_key):
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)
    
    try:
        ticket = NewTicket.objects.get(key=ticket_key)
        
        # Check if user has scanner access for this ticket's event
        if not has_scanner_access(request.user, ticket.event):
            return JsonResponse({'error': 'No tienes permisos para marcar tickets de este evento'}, status=403)
        
        if ticket.is_used:
            return JsonResponse({'error': 'El bono ya fue usado'}, status=400)
        
        ticket.is_used = True
        ticket.used_at = timezone.now()
        ticket.scanned_by = request.user
        ticket.save()
        
        return JsonResponse(_ticket_check_response(ticket))
    except NewTicket.DoesNotExist:
        return JsonResponse({'error': 'Bono no encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def mark_ticket_left(request, ticket_key):
    """Mark this ticket's holder as having left the venue (salió)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)
    try:
        ticket = NewTicket.objects.get(key=ticket_key)
        if not has_scanner_access(request.user, ticket.event):
            return JsonResponse({'error': 'No tienes permisos'}, status=403)
        if not ticket.is_used:
            return JsonResponse({'error': 'El bono no está marcado como usado'}, status=400)
        if getattr(ticket, 'holder_left', False):
            return JsonResponse({'error': 'Ya está registrado como que salió'}, status=400)
        ticket.holder_left = True
        ticket.left_at = timezone.now()
        ticket.save()
        event = ticket.event
        if event.venue_capacity is not None:
            event.attendees_left += 1
            event.save(update_fields=['attendees_left'])
        return JsonResponse(_ticket_check_response(ticket))
    except NewTicket.DoesNotExist:
        return JsonResponse({'error': 'Bono no encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def mark_ticket_returned(request, ticket_key):
    """Mark this ticket's holder as having returned to the venue (volvió a entrar)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)
    try:
        ticket = NewTicket.objects.get(key=ticket_key)
        if not has_scanner_access(request.user, ticket.event):
            return JsonResponse({'error': 'No tienes permisos'}, status=403)
        if not getattr(ticket, 'holder_left', False):
            return JsonResponse({'error': 'Este bono no estaba registrado como que salió'}, status=400)
        ticket.holder_left = False
        ticket.left_at = None
        ticket.save()
        event = ticket.event
        if event.venue_capacity is not None and event.attendees_left > 0:
            event.attendees_left -= 1
            event.save(update_fields=['attendees_left'])
        return JsonResponse(_ticket_check_response(ticket))
    except NewTicket.DoesNotExist:
        return JsonResponse({'error': 'Bono no encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def increment_attendees_left(request, event_slug):
    """Increment attendees left counter when someone leaves"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)
    
    try:
        from events.models import Event
        event = Event.objects.get(slug=event_slug)
        
        # Check if user has scanner access for this event
        if not has_scanner_access(request.user, event):
            return JsonResponse({'error': 'No tienes permisos para acceder al scanner de este evento'}, status=403)
        
        # Check if event has venue capacity tracking enabled
        if event.venue_capacity is None:
            return JsonResponse({'error': 'Este evento no tiene capacidad de venue configurada'}, status=400)
        
        # Increment attendees left counter
        event.attendees_left += 1
        event.save(update_fields=['attendees_left'])
        
        return JsonResponse({
            'success': True,
            'message': 'Contador de salidas incrementado exitosamente',
            'venue_occupancy': event.venue_occupancy,
            'venue_capacity': event.venue_capacity,
            'attendees_left': event.attendees_left,
            'occupancy_percentage': event.occupancy_percentage
        })
            
    except Event.DoesNotExist:
        return JsonResponse({'error': 'Evento no encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@login_required
def decrement_attendees_left(request, event_slug):
    """Decrement attendees left counter when someone returns"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)
    
    try:
        from events.models import Event
        event = Event.objects.get(slug=event_slug)
        
        # Check if user has scanner access for this event
        if not has_scanner_access(request.user, event):
            return JsonResponse({'error': 'No tienes permisos para acceder al scanner de este evento'}, status=403)
        
        # Check if event has venue capacity tracking enabled
        if event.venue_capacity is None:
            return JsonResponse({'error': 'Este evento no tiene capacidad de venue configurada'}, status=400)
        
        # Check if there are attendees left to decrement
        if event.attendees_left <= 0:
            return JsonResponse({'error': 'No hay asistentes que hayan salido para que regresen'}, status=400)
        
        # Decrement attendees left counter
        event.attendees_left -= 1
        event.save(update_fields=['attendees_left'])
        
        return JsonResponse({
            'success': True,
            'message': 'Contador de salidas decrementado exitosamente',
            'venue_occupancy': event.venue_occupancy,
            'venue_capacity': event.venue_capacity,
            'attendees_left': event.attendees_left,
            'occupancy_percentage': event.occupancy_percentage
        })
            
    except Event.DoesNotExist:
        return JsonResponse({'error': 'Evento no encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@login_required
def update_ticket_notes(request, ticket_key):
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)
    
    try:
        ticket = NewTicket.objects.get(key=ticket_key)
        
        # Check if user has scanner access for this ticket's event
        if not has_scanner_access(request.user, ticket.event):
            return JsonResponse({'error': 'No tienes permisos para actualizar tickets de este evento'}, status=403)
        
        # Get notes from request
        notes = request.POST.get('notes', '')
        
        # Update ticket notes
        ticket.notes = notes
        
        # Handle new file uploads
        if 'photos' in request.FILES:
            for photo in request.FILES.getlist('photos'):
                # Create TicketPhoto instance
                from tickets.models import TicketPhoto
                TicketPhoto.objects.create(
                    ticket=ticket,
                    photo=photo,
                    uploaded_by=request.user
                )
        
        ticket.save()
        
        return JsonResponse({
            'success': True,
            'notes': ticket.notes,
            'photos': [
                {
                    'id': photo.id,
                    'url': photo.photo.url,
                    'name': photo.photo.name.split('/')[-1],  # Get filename
                    'uploaded_at': photo.created_at.isoformat() if photo.created_at else None,
                    'uploaded_by': photo.uploaded_by.get_full_name() or photo.uploaded_by.username
                }
                for photo in ticket.ticket_photos.all()
            ]
        })
        
    except ObjectDoesNotExist:
        return JsonResponse({'error': 'Bono no encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@login_required
def delete_ticket_photo(request, ticket_key):
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)
    
    try:
        ticket = NewTicket.objects.get(key=ticket_key)
        
        # Check if user has scanner access for this ticket's event
        if not has_scanner_access(request.user, ticket.event):
            return JsonResponse({'error': 'No tienes permisos para actualizar tickets de este evento'}, status=403)
        
        # Get photo ID to delete
        photo_id = request.POST.get('photo_id')
        
        if not photo_id:
            return JsonResponse({'error': 'ID de foto requerido'}, status=400)
        
        try:
            # Get the photo to delete
            from tickets.models import TicketPhoto
            photo = TicketPhoto.objects.get(id=photo_id, ticket=ticket)
            
            # Delete the photo (this will also delete the file from S3)
            photo.delete()
            
        except TicketPhoto.DoesNotExist:
            return JsonResponse({'error': 'Foto no encontrada'}, status=404)
        
        return JsonResponse({
            'success': True,
            'notes': ticket.notes,
            'photos': [
                {
                    'id': photo.id,
                    'url': photo.photo.url,
                    'name': photo.photo.name.split('/')[-1],  # Get filename
                    'uploaded_at': photo.created_at.isoformat() if photo.created_at else None,
                    'uploaded_by': photo.uploaded_by.get_full_name() or photo.uploaded_by.username
                }
                for photo in ticket.ticket_photos.all()
            ]
        })
        
    except ObjectDoesNotExist:
        return JsonResponse({'error': 'Bono no encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500) 