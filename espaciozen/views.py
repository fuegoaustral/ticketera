import os
from datetime import datetime, timedelta
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.utils import timezone
import json
import requests
import jwt
import time
import hashlib


def get_user_hash(email):
    """
    Genera un hash SHA256 del email del usuario para identificación privada.
    El mismo email siempre genera el mismo hash (determinístico).
    """
    return hashlib.sha256(email.encode('utf-8')).hexdigest()


def get_google_access_token():
    """
    Obtiene un access token usando Service Account JWT
    Requiere las siguientes variables de entorno:
    - ESPACIO_ZEN_CLIENT_EMAIL: El email del service account
    - ESPACIO_ZEN_PRIVATE_KEY: La clave privada (con \n preservados)
    - ESPACIO_ZEN_PRIVATE_KEY_ID: El ID de la clave privada (opcional)
    """
    client_email = os.environ.get('ESPACIO_ZEN_CLIENT_EMAIL', '')
    private_key = os.environ.get('ESPACIO_ZEN_PRIVATE_KEY', '')
    
    if not client_email or not private_key:
        raise ValueError('ESPACIO_ZEN_CLIENT_EMAIL y ESPACIO_ZEN_PRIVATE_KEY deben estar configurados')
    
    # Restaurar los saltos de línea en la clave privada
    private_key = private_key.replace('\\n', '\n')
    
    # Crear el JWT
    now = int(time.time())
    jwt_payload = {
        'iss': client_email,
        'sub': client_email,
        'aud': 'https://www.googleapis.com/oauth2/v4/token',
        'iat': now,
        'exp': now + 3600,  # Token válido por 1 hora
        'scope': 'https://www.googleapis.com/auth/calendar'
    }
    
    # Firmar el JWT
    token = jwt.encode(jwt_payload, private_key, algorithm='RS256')
    
    # Intercambiar JWT por access token
    response = requests.post(
        'https://www.googleapis.com/oauth2/v4/token',
        data={
            'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
            'assertion': token
        },
        timeout=10
    )
    
    if response.status_code != 200:
        error_data = response.json() if response.content else {}
        raise Exception(f'Error obteniendo access token: {error_data.get("error_description", "Error desconocido")}')
    
    return response.json()['access_token']


@login_required
def espaciozen_home(request):
    """Vista principal que muestra el calendario de Google"""
    calendar_id = "espaciozenlasedefa@gmail.com"
    
    # Obtener reservas del usuario
    reservas_usuario = []
    try:
        access_token = get_google_access_token()
        email_usuario = request.user.email
        nombre_usuario = request.user.first_name or request.user.username
        user_hash = get_user_hash(email_usuario)
        
        # Buscar eventos del usuario usando su hash (más confiable y privado que el nombre)
        url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
        params = {
            'timeMin': datetime.now(timezone.get_current_timezone()).isoformat(),
            'singleEvents': True,
            'orderBy': 'startTime',
            'maxResults': 50
        }
        headers = {
            'Authorization': f'Bearer {access_token}'
        }
        
        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            if response.status_code == 200:
                events = response.json().get('items', [])
                # Filtrar eventos que pertenecen al usuario usando su hash
                for event in events:
                    summary = event.get('summary', '')
                    description = event.get('description', '')
                    # Verificar si el evento termina con el hash del usuario (identificador único y privado)
                    # El hash está al final de la descripción sin prefijo
                    if description.endswith(user_hash):
                        start = event.get('start', {}).get('dateTime', '')
                        end = event.get('end', {}).get('dateTime', '')
                        # Limpiar la descripción removiendo el hash del final y el nombre
                        descripcion_limpia = description.replace(f'Reservado por: {nombre_usuario}', '').rstrip()
                        # Remover el hash del final (puede haber varios saltos de línea antes)
                        if descripcion_limpia.endswith(user_hash):
                            # Remover el hash y los saltos de línea que lo preceden
                            descripcion_limpia = descripcion_limpia[:-len(user_hash)].rstrip()
                        # Remover líneas vacías adicionales
                        descripcion_limpia = '\n'.join([line for line in descripcion_limpia.split('\n') if line.strip()])
                        reservas_usuario.append({
                            'id': event.get('id'),
                            'titulo': summary.replace(f' - {nombre_usuario}', '').strip(),
                            'descripcion': descripcion_limpia,
                            'fecha_inicio': start,
                            'fecha_fin': end,
                            'event_id': event.get('id')
                        })
        except Exception as e:
            # Si hay error, simplemente no mostramos las reservas
            pass
    except Exception as e:
        # Si hay error de autenticación, continuamos sin reservas
        pass
    
    context = {
        'calendar_id': calendar_id,
        'reservas': reservas_usuario,
    }
    
    return render(request, 'espaciozen/home.html', context)


@login_required
@require_http_methods(["POST"])
def verificar_disponibilidad(request):
    """Verifica si hay disponibilidad en el rango de fechas solicitado consultando Google Calendar"""
    try:
        data = json.loads(request.body)
        fecha_inicio_str = data.get('fecha_inicio')
        fecha_fin_str = data.get('fecha_fin')
        event_id_excluir = data.get('event_id')  # Para excluir el evento actual al editar
        
        if not fecha_inicio_str or not fecha_fin_str:
            return JsonResponse({'error': 'Fechas requeridas'}, status=400)
        
        fecha_inicio = datetime.fromisoformat(fecha_inicio_str.replace('Z', '+00:00'))
        fecha_fin = datetime.fromisoformat(fecha_fin_str.replace('Z', '+00:00'))
        
        # Convertir a timezone aware si no lo es
        if timezone.is_naive(fecha_inicio):
            fecha_inicio = timezone.make_aware(fecha_inicio)
        if timezone.is_naive(fecha_fin):
            fecha_fin = timezone.make_aware(fecha_fin)
        
        calendar_id = "espaciozenlasedefa@gmail.com"
        
        # Obtener access token
        try:
            access_token = get_google_access_token()
        except Exception as e:
            return JsonResponse({
                'error': f'Error de autenticación: {str(e)}'
            }, status=500)
        
        # Consultar Google Calendar API
        url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
        params = {
            'timeMin': fecha_inicio.isoformat(),
            'timeMax': fecha_fin.isoformat(),
            'singleEvents': True,
            'orderBy': 'startTime'
        }
        headers = {
            'Authorization': f'Bearer {access_token}'
        }
        
        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            if response.status_code == 200:
                events = response.json().get('items', [])
                # Filtrar el evento actual si se está editando
                if event_id_excluir:
                    events = [e for e in events if e.get('id') != event_id_excluir]
                if events:
                    return JsonResponse({
                        'disponible': False,
                        'mensaje': 'Ya existe un evento en Google Calendar en este horario'
                    })
            elif response.status_code == 403:
                return JsonResponse({
                    'error': 'No se tienen permisos para acceder al calendario. Asegúrate de que el calendario esté compartido con el service account.'
                }, status=403)
            else:
                error_data = response.json() if response.content else {}
                return JsonResponse({
                    'error': f'Error al consultar el calendario: {error_data.get("error", {}).get("message", "Error desconocido")}'
                }, status=response.status_code)
        except requests.exceptions.RequestException as e:
            return JsonResponse({
                'error': f'Error al consultar Google Calendar: {str(e)}'
            }, status=500)
        
        return JsonResponse({
            'disponible': True,
            'mensaje': 'El horario está disponible'
        })
        
    except ValueError as e:
        return JsonResponse({'error': f'Formato de fecha inválido: {str(e)}'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
def crear_reserva(request):
    """Crea una nueva reserva directamente en Google Calendar usando Service Account"""
    try:
        data = json.loads(request.body)
        titulo = data.get('titulo')
        descripcion = data.get('descripcion', '')
        fecha_inicio_str = data.get('fecha_inicio')
        fecha_fin_str = data.get('fecha_fin')
        
        if not titulo or not fecha_inicio_str or not fecha_fin_str:
            return JsonResponse({'error': 'Datos incompletos'}, status=400)
        
        fecha_inicio = datetime.fromisoformat(fecha_inicio_str.replace('Z', '+00:00'))
        fecha_fin = datetime.fromisoformat(fecha_fin_str.replace('Z', '+00:00'))
        
        # Convertir a timezone aware si no lo es
        if timezone.is_naive(fecha_inicio):
            fecha_inicio = timezone.make_aware(fecha_inicio)
        if timezone.is_naive(fecha_fin):
            fecha_fin = timezone.make_aware(fecha_fin)
        
        calendar_id = "espaciozenlasedefa@gmail.com"
        
        # Obtener access token
        try:
            access_token = get_google_access_token()
        except Exception as e:
            return JsonResponse({
                'error': f'Error de autenticación: {str(e)}'
            }, status=500)
        
        # Verificar conflictos antes de crear
        url_check = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
        params_check = {
            'timeMin': fecha_inicio.isoformat(),
            'timeMax': fecha_fin.isoformat(),
            'singleEvents': True,
            'orderBy': 'startTime'
        }
        headers = {
            'Authorization': f'Bearer {access_token}'
        }
        
        try:
            response_check = requests.get(url_check, params=params_check, headers=headers, timeout=10)
            if response_check.status_code == 200:
                events = response_check.json().get('items', [])
                if events:
                    return JsonResponse({
                        'error': 'Ya existe un evento en Google Calendar en este horario'
                    }, status=400)
            elif response_check.status_code != 403:
                # Si es 403, continuamos de todos modos (puede ser que no tenga permisos de lectura pero sí de escritura)
                pass
        except requests.exceptions.RequestException as e:
            # Continuamos de todos modos
            pass
        
        # Obtener nombre del usuario (solo first_name) y generar hash del email para identificación privada
        nombre_usuario = request.user.first_name or request.user.username
        email_usuario = request.user.email
        user_hash = get_user_hash(email_usuario)
        
        # Crear el evento en Google Calendar
        url_create = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
        
        # Incluir el nombre del usuario en el título y hash al final de la descripción para identificación única y privada
        titulo_con_usuario = f"{titulo} - {nombre_usuario}"
        # El hash se guarda al final de la descripción con varios saltos de línea y sin prefijo
        descripcion_completa = f"Reservado por: {nombre_usuario}"
        if descripcion:
            descripcion_completa += f"\n\n{descripcion}"
        # Agregar el hash al final con varios saltos de línea
        descripcion_completa += f"\n\n\n\n{user_hash}"
        
        event_data = {
            'summary': titulo_con_usuario,
            'description': descripcion_completa,
            'start': {
                'dateTime': fecha_inicio.isoformat(),
                'timeZone': 'America/Argentina/Buenos_Aires'
            },
            'end': {
                'dateTime': fecha_fin.isoformat(),
                'timeZone': 'America/Argentina/Buenos_Aires'
            }
        }
        
        try:
            response_create = requests.post(
                url_create,
                json=event_data,
                headers={
                    'Authorization': f'Bearer {access_token}',
                    'Content-Type': 'application/json'
                },
                timeout=10
            )
            
            if response_create.status_code == 200:
                event = response_create.json()
                return JsonResponse({
                    'success': True,
                    'mensaje': 'Reserva creada exitosamente en Google Calendar',
                    'event_id': event.get('id')
                })
            elif response_create.status_code == 403:
                return JsonResponse({
                    'error': 'No se tienen permisos para crear eventos. Asegúrate de que el calendario esté compartido con el service account y tenga permisos de "Make changes to events".'
                }, status=403)
            else:
                error_data = response_create.json() if response_create.content else {}
                error_message = error_data.get('error', {}).get('message', 'Error desconocido')
                return JsonResponse({
                    'error': f'Error al crear el evento: {error_message}'
                }, status=response_create.status_code)
        except requests.exceptions.RequestException as e:
            return JsonResponse({
                'error': f'Error al crear el evento en Google Calendar: {str(e)}'
            }, status=500)
        
    except ValueError as e:
        return JsonResponse({'error': f'Formato de fecha inválido: {str(e)}'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_http_methods(["GET"])
def listar_reservas(request):
    """Lista las reservas del usuario actual"""
    try:
        calendar_id = "espaciozenlasedefa@gmail.com"
        access_token = get_google_access_token()
        email_usuario = request.user.email
        nombre_usuario = request.user.first_name or request.user.username
        user_hash = get_user_hash(email_usuario)
        
        # Buscar eventos del usuario usando su hash
        url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
        params = {
            'timeMin': datetime.now(timezone.get_current_timezone()).isoformat(),
            'singleEvents': True,
            'orderBy': 'startTime',
            'maxResults': 50
        }
        headers = {
            'Authorization': f'Bearer {access_token}'
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code == 200:
            events = response.json().get('items', [])
            reservas = []
            for event in events:
                summary = event.get('summary', '')
                description = event.get('description', '')
                # Verificar usando el hash del usuario (identificador único y privado)
                # El hash está al final de la descripción sin prefijo
                if description.endswith(user_hash):
                    start = event.get('start', {}).get('dateTime', '')
                    end = event.get('end', {}).get('dateTime', '')
                    # Limpiar la descripción
                    descripcion_limpia = description.replace(f'Reservado por: {nombre_usuario}', '').rstrip()
                    # Remover el hash del final
                    if descripcion_limpia.endswith(user_hash):
                        descripcion_limpia = descripcion_limpia[:-len(user_hash)].rstrip()
                    descripcion_limpia = '\n'.join([line for line in descripcion_limpia.split('\n') if line.strip()])
                    reservas.append({
                        'id': event.get('id'),
                        'titulo': summary.replace(f' - {nombre_usuario}', '').strip(),
                        'descripcion': descripcion_limpia,
                        'fecha_inicio': start,
                        'fecha_fin': end,
                        'event_id': event.get('id')
                    })
            return JsonResponse({'reservas': reservas}, status=200)
        else:
            return JsonResponse({'error': 'Error al obtener reservas'}, status=response.status_code)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
def editar_reserva(request):
    """Edita una reserva existente en Google Calendar"""
    try:
        data = json.loads(request.body)
        event_id = data.get('event_id')
        titulo = data.get('titulo')
        descripcion = data.get('descripcion', '')
        fecha_inicio_str = data.get('fecha_inicio')
        fecha_fin_str = data.get('fecha_fin')
        
        if not event_id or not titulo or not fecha_inicio_str or not fecha_fin_str:
            return JsonResponse({'error': 'Datos incompletos'}, status=400)
        
        fecha_inicio = datetime.fromisoformat(fecha_inicio_str.replace('Z', '+00:00'))
        fecha_fin = datetime.fromisoformat(fecha_fin_str.replace('Z', '+00:00'))
        
        if timezone.is_naive(fecha_inicio):
            fecha_inicio = timezone.make_aware(fecha_inicio)
        if timezone.is_naive(fecha_fin):
            fecha_fin = timezone.make_aware(fecha_fin)
        
        calendar_id = "espaciozenlasedefa@gmail.com"
        access_token = get_google_access_token()
        email_usuario = request.user.email
        nombre_usuario = request.user.first_name or request.user.username
        user_hash = get_user_hash(email_usuario)
        
        # Verificar que el evento pertenece al usuario
        url_get = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}"
        headers = {'Authorization': f'Bearer {access_token}'}
        
        response_get = requests.get(url_get, headers=headers, timeout=10)
        if response_get.status_code != 200:
            return JsonResponse({'error': 'Reserva no encontrada'}, status=404)
        
        event = response_get.json()
        description = event.get('description', '')
        
        # Verificar usando el hash del usuario (identificador único y privado)
        # El hash está al final de la descripción sin prefijo
        if not description.endswith(user_hash):
            return JsonResponse({'error': 'No tienes permisos para editar esta reserva'}, status=403)
        
        # Actualizar el evento
        titulo_con_usuario = f"{titulo} - {nombre_usuario}"
        descripcion_completa = f"Reservado por: {nombre_usuario}"
        if descripcion:
            descripcion_completa += f"\n\n{descripcion}"
        # Agregar el hash al final con varios saltos de línea
        descripcion_completa += f"\n\n\n\n{user_hash}"
        
        event_data = {
            'summary': titulo_con_usuario,
            'description': descripcion_completa,
            'start': {
                'dateTime': fecha_inicio.isoformat(),
                'timeZone': 'America/Argentina/Buenos_Aires'
            },
            'end': {
                'dateTime': fecha_fin.isoformat(),
                'timeZone': 'America/Argentina/Buenos_Aires'
            }
        }
        
        url_update = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}"
        response_update = requests.put(
            url_update,
            json=event_data,
            headers={
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            },
            timeout=10
        )
        
        if response_update.status_code == 200:
            return JsonResponse({
                'success': True,
                'mensaje': 'Reserva actualizada exitosamente'
            })
        else:
            error_data = response_update.json() if response_update.content else {}
            return JsonResponse({
                'error': f'Error al actualizar la reserva: {error_data.get("error", {}).get("message", "Error desconocido")}'
            }, status=response_update.status_code)
        
    except ValueError as e:
        return JsonResponse({'error': f'Formato de fecha inválido: {str(e)}'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
def borrar_reserva(request):
    """Borra una reserva de Google Calendar"""
    try:
        data = json.loads(request.body)
        event_id = data.get('event_id')
        
        if not event_id:
            return JsonResponse({'error': 'ID de reserva requerido'}, status=400)
        
        calendar_id = "espaciozenlasedefa@gmail.com"
        access_token = get_google_access_token()
        email_usuario = request.user.email
        user_hash = get_user_hash(email_usuario)
        
        # Verificar que el evento pertenece al usuario
        url_get = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}"
        headers = {'Authorization': f'Bearer {access_token}'}
        
        response_get = requests.get(url_get, headers=headers, timeout=10)
        if response_get.status_code != 200:
            return JsonResponse({'error': 'Reserva no encontrada'}, status=404)
        
        event = response_get.json()
        description = event.get('description', '')
        
        # Verificar usando el hash del usuario (identificador único y privado)
        # El hash está al final de la descripción sin prefijo
        if not description.endswith(user_hash):
            return JsonResponse({'error': 'No tienes permisos para borrar esta reserva'}, status=403)
        
        # Eliminar el evento
        url_delete = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}"
        response_delete = requests.delete(url_delete, headers=headers, timeout=10)
        
        if response_delete.status_code == 204:
            return JsonResponse({
                'success': True,
                'mensaje': 'Reserva eliminada exitosamente'
            })
        else:
            return JsonResponse({
                'error': 'Error al eliminar la reserva'
            }, status=response_delete.status_code)
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
