"""Historial de cada propuesta de Arte: quién cambió qué y cuándo."""
from datetime import datetime, timedelta, timezone as dt_timezone

from django.db.models import Count, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.text import Truncator

from .models import ArtworkEvent, ArtworkVisit

STATUS, EDIT, ESTAFA, CLOSE, MESSAGE = (
    ArtworkEvent.Kind.STATUS, ArtworkEvent.Kind.EDIT, ArtworkEvent.Kind.ESTAFA, ArtworkEvent.Kind.CLOSE, ArtworkEvent.Kind.MESSAGE,
)

# Los bloques con el contenido de la propuesta: sus cambios son los importantes.
# Fotos, archivos, equipo y proveedores quedan en el historial completo.
IMPORTANT_SECTIONS = {'Detalles', 'Folleto', 'Checkout', 'Declaración', 'Beca', 'Rendición de beca'}
# Varios cambios seguidos de la misma persona en el mismo bloque son una sola línea.
MERGE_WINDOW = timedelta(minutes=30)

# Las novedades se cuentan desde la última visita de cada persona. Quien todavía no abrió una propuesta
# las cuenta desde que existe el aviso: lo anterior no aparece como nuevo.
NEWS_SINCE = datetime(2026, 11, 14, 16, 0, tzinfo=dt_timezone.utc)

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


def with_news(artworks, user):
    """Anota en cada propuesta cuántos cambios de otras personas hubo desde la última visita de `user`."""
    seen = ArtworkVisit.objects.filter(artwork=OuterRef('pk'), user=user).values('seen_at')[:1]
    news = (
        ArtworkEvent.objects.filter(artwork=OuterRef('pk'), created_at__gt=OuterRef('last_seen'))
        .exclude(actor=user).order_by().values('artwork').annotate(total=Count('pk')).values('total')
    )
    return artworks.annotate(
        last_seen=Coalesce(Subquery(seen), Value(NEWS_SINCE)),
    ).annotate(news_count=Coalesce(Subquery(news, output_field=IntegerField()), 0))


def visit(artwork, user, entries):
    """Marca en `entries` lo nuevo para `user`, anota la visita y devuelve (cuántas novedades, visita anterior)."""
    previous = ArtworkVisit.objects.filter(artwork=artwork, user=user).values_list('seen_at', flat=True).first()
    since = previous or NEWS_SINCE
    total = 0
    for entry in entries:
        entry.is_new = bool(entry.pk and entry.created_at > since and entry.actor_id != user.pk)
        total += entry.is_new
    ArtworkVisit.objects.update_or_create(artwork=artwork, user=user, defaults={'seen_at': timezone.now()})
    return total, previous


def mark_all_seen(artworks, user):
    """Deja todas las propuestas de `artworks` como vistas por `user`."""
    now = timezone.now()
    ArtworkVisit.objects.bulk_create(
        [ArtworkVisit(artwork_id=pk, user=user, seen_at=now) for pk in artworks.values_list('pk', flat=True)],
        update_conflicts=True, unique_fields=['artwork', 'user'], update_fields=['seen_at'],
    )
