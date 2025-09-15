from django import template
from django.contrib import messages

register = template.Library()


@register.filter
def filter_confusing_messages(messages_list):
    """
    Filtra mensajes confusos de login/logout de allauth
    """
    confusing_messages = [
        'iniciado sesión',
        'has cerrado sesión', 
        'ha cerrado sesión',
        'sesión iniciada',
        'sesión cerrada',
        'logged in',
        'logged out',
        'signed in',
        'signed out'
    ]
    
    filtered_messages = []
    for message in messages_list:
        message_text = str(message).lower()
        is_confusing = any(confusing in message_text for confusing in confusing_messages)
        
        if not is_confusing:
            filtered_messages.append(message)
    
    return filtered_messages
