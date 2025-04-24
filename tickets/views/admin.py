from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import render
from django.http import JsonResponse
from tickets.models import NewTicket
from django.core.exceptions import ObjectDoesNotExist

def is_admin_or_puerta(user):
    return user.is_superuser or user.groups.filter(name='Puerta').exists()

@user_passes_test(is_admin_or_puerta)
def scan_tickets(request):
    return render(request, 'mi_fuego/admin/scan_tickets.html')

@user_passes_test(is_admin_or_puerta)
def check_ticket(request, ticket_key):
    try:
        ticket = NewTicket.objects.get(key=ticket_key)
        return JsonResponse({
            'key': ticket.key,
            'ticket_type': str(ticket.ticket_type),
            'is_used': ticket.is_used,
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

@user_passes_test(is_admin_or_puerta)
def mark_ticket_used(request, ticket_key):
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)
    
    try:
        ticket = NewTicket.objects.get(key=ticket_key)
        if ticket.is_used:
            return JsonResponse({'error': 'El bono ya fue usado'}, status=400)
        
        ticket.is_used = True
        ticket.save()
        
        return JsonResponse({
            'key': ticket.key,
            'ticket_type': str(ticket.ticket_type),
            'is_used': ticket.is_used,
            'owner_name': f"{ticket.owner.first_name} {ticket.owner.last_name}" if ticket.owner else "Sin asignar"
        })
    except ObjectDoesNotExist:
        return JsonResponse({'error': 'Bono no encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500) 