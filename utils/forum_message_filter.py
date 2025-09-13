from django.contrib import messages


class ForumMessageFilterMiddleware:
    """
    Middleware que filtra los mensajes del foro cuando se está en el admin
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Procesar la request
        response = self.get_response(request)
        
        # Si estamos en el admin, filtrar los mensajes del foro
        if request.path.startswith('/admin/'):
            # Obtener todos los mensajes
            storage = messages.get_messages(request)
            filtered_messages = []
            
            for message in storage:
                # Solo mantener mensajes que NO sean del foro
                if "Mensaje publicado correctamente" not in str(message):
                    filtered_messages.append(message)
            
            # Limpiar los mensajes existentes
            storage.used = True
            
            # Agregar solo los mensajes filtrados
            for message in filtered_messages:
                messages.add_message(request, message.level, message.message, extra_tags=message.extra_tags)
        
        return response
