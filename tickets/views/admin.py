from django.contrib.auth.decorators import user_passes_test, login_required
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponseForbidden
from django.utils import timezone
from tickets.models import NewTicket
from events.models import Event
from django.core.exceptions import ObjectDoesNotExist

def is_admin_or_puerta(user):
    return user.is_superuser or user.groups.filter(name='Puerta').exists()

def has_scanner_access(user, event=None):
    """Check if user has scanner access for an event"""
    if user.is_superuser:
        return True
    if event and event.access_scanner.filter(id=user.id).exists():
        return True
    # Fallback to old group-based logic for backward compatibility
    return user.groups.filter(name='Puerta').exists()

@user_passes_test(is_admin_or_puerta)
def scan_tickets(request):
    return render(request, 'mi_fuego/admin/scan_tickets.html')

@login_required
def scan_tickets_event(request, event_slug):
    """Scanner for a specific event with new access control"""
    event = get_object_or_404(Event, slug=event_slug)
    
    # Check if user has scanner access for this event
    if not has_scanner_access(request.user, event):
        return HttpResponseForbidden("No tienes permisos para acceder al scanner de este evento")
    
    context = {
        'event': event,
    }
    return render(request, 'mi_fuego/admin/scan_tickets.html', context)

@login_required
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
        
        return JsonResponse({
            'key': ticket.key,
            'ticket_type': str(ticket.ticket_type),
            'is_used': ticket.is_used,
            'used_at': ticket.used_at.isoformat() if ticket.used_at else None,
            'scanned_by': {
                'id': ticket.scanned_by.id,
                'username': ticket.scanned_by.username,
                'full_name': ticket.scanned_by.get_full_name() or ticket.scanned_by.username,
                'email': ticket.scanned_by.email
            } if ticket.scanned_by else None,
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
            ],
            'owner_name': f"{ticket.owner.first_name} {ticket.owner.last_name}" if ticket.owner else None,
            'user_info': {
                'first_name': ticket.holder.first_name,
                'last_name': ticket.holder.last_name,
                'document_type': ticket.holder.profile.document_type,
                'document_number': ticket.holder.profile.document_number
            } if ticket.holder else None
        })
    except ObjectDoesNotExist:
        return JsonResponse({'error': 'Bono no encontrado'}, status=404)
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
        
        return JsonResponse({
            'key': ticket.key,
            'ticket_type': str(ticket.ticket_type),
            'is_used': ticket.is_used,
            'used_at': ticket.used_at.isoformat() if ticket.used_at else None,
            'scanned_by': {
                'id': ticket.scanned_by.id,
                'username': ticket.scanned_by.username,
                'full_name': ticket.scanned_by.get_full_name() or ticket.scanned_by.username,
                'email': ticket.scanned_by.email
            } if ticket.scanned_by else None,
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
            ],
            'owner_name': f"{ticket.owner.first_name} {ticket.owner.last_name}" if ticket.owner else "Sin asignar",
            'user_info': {
                'first_name': ticket.holder.first_name,
                'last_name': ticket.holder.last_name,
                'document_type': ticket.holder.profile.document_type,
                'document_number': ticket.holder.profile.document_number
            } if ticket.holder else None
        })
    except ObjectDoesNotExist:
        return JsonResponse({'error': 'Bono no encontrado'}, status=404)
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