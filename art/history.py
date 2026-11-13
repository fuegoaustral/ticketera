"""Historial de cada propuesta de Arte: quién cambió qué y cuándo."""
from datetime import timedelta

from django.utils import timezone
from django.utils.text import Truncator

from .models import ArtworkEvent

STATUS, EDIT, ESTAFA, CLOSE = ArtworkEvent.Kind.STATUS, ArtworkEvent.Kind.EDIT, ArtworkEvent.Kind.ESTAFA, ArtworkEvent.Kind.CLOSE

# Los bloques con el contenido de la propuesta: sus cambios son los importantes.
# Fotos, archivos, equipo y proveedores quedan en el historial completo.
IMPORTANT_SECTIONS = {'Detalles', 'Folleto', 'Checkout', 'Declaración', 'Beca', 'Rendición de beca'}
# Varios cambios seguidos de la misma persona en el mismo bloque son una sola línea.
MERGE_WINDOW = timedelta(minutes=30)

# Los cierres del programa, en el orden del proceso.
CLOSURES = (
    ('registration', 'Inscripción'), ('proposal', 'Propuesta'), ('grant', 'Beca'), ('guide', 'Folleto'),
    ('logistics', 'Ingreso y proveedores'), ('gallery', 'Galería'),
    ('understanding_letter_digital', 'Declaración digital'), ('understanding_letter_physical', 'Declaración física'),
    ('checkout', 'Checkout'), ('grant_report', 'Rendición de beca'),
)


def _actor(user):
    return user if user is not None and getattr(user, 'is_authenticated', False) else None


def log(artwork, kind, actor, summary, detail='', important=True):
    return ArtworkEvent.objects.create(
        artwork=artwork, kind=kind, actor=_actor(actor), summary=summary, detail=detail, important=important,
    )


def log_edit(artwork, section, actor, fields=(), kind=EDIT, important=None):
    """Anota un cambio en un bloque, con los campos que cambiaron si se saben."""
    actor = _actor(actor)
    summary = f'Editó {section}'
    fields = [Truncator(str(field)).chars(48) for field in fields]
    now = timezone.now()
    last = artwork.history.order_by('-created_at', '-pk').first()
    if (
        last and last.kind == kind and last.summary == summary and actor and last.actor_id == actor.pk
        and now - last.created_at <= MERGE_WINDOW
    ):
        merged = [field for field in last.detail.split(', ') if field]
        merged += [field for field in fields if field not in merged]
        last.detail, last.created_at = ', '.join(merged), now
        last.save(update_fields=['detail', 'created_at'])
        return last
    return log(
        artwork, kind, actor, summary, detail=', '.join(fields),
        important=section in IMPORTANT_SECTIONS if important is None else important,
    )


def timeline(artwork, program):
    """El historial guardado más los cierres del programa que ya pasaron, de lo más nuevo a lo más viejo."""
    events = list(artwork.history.select_related('actor'))
    now = timezone.now()
    for block, label in CLOSURES:
        if block in ('grant', 'grant_report') and not program.grants_enabled:
            continue
        deadline = program.deadline_for(block)
        if deadline and artwork.created_at <= deadline <= now:
            events.append(ArtworkEvent(
                artwork=artwork, kind=CLOSE, summary=f'Cierre de {label}', created_at=deadline, important=True,
            ))
    return sorted(events, key=lambda event: event.created_at, reverse=True)
