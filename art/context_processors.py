from django.urls import reverse

from .models import ArtProgram


def art_share(request):
    """Al compartir un link de Mi Fuego → Arte, la vista previa muestra la convocatoria vigente.

    Las páginas de Arte piden sesión, así que quien arma la vista previa (WhatsApp, etc.) llega al
    login con ?next=/mi-fuego/arte/…: ese caso también cuenta.
    """
    prefix = reverse('art_dashboard')
    if not (request.path.startswith(prefix) or request.GET.get('next', '').startswith(prefix)):
        return {}
    program = ArtProgram.objects.filter(is_current=True).select_related('event').order_by('-event__start').first()
    return {'og_art_program': program} if program else {}
