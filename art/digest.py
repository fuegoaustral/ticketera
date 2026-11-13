"""Emails de las obras para ESTAFA: obra nueva al momento y un resumen horario de cambios.

Cuentan como cambio sólo los que hace el equipo de la instalación (editar la propuesta, la beca,
el checkout, subir fotos o archivos). Lo que hace ESTAFA al coordinar no se avisa.
"""
import hashlib
import logging
from collections import OrderedDict

from django.conf import settings
from django.core.mail import get_connection
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from tickets.models import MessageIdempotency
from user_profile.display import display_name
from templated_email import get_templated_mail

from . import history
from .estafa import estafa_members, is_fuego_austral
from .models import ArtworkChange, EstafaNotificationPreference


def _send_one(connection, template_name, email, context):
    """Un email a una sola persona, por una conexión SMTP compartida (muchos envíos seguidos)."""
    message = get_templated_mail(
        template_name=template_name, context={**context, 'base_url': settings.APP_URL},
        from_email=settings.DEFAULT_FROM_EMAIL, to=[email],
    )
    connection.send_messages([message])


def _prefs_path():
    return f"{reverse('profile')}#emails-estafa"


def _preferences(users):
    saved = {pref.user_id: pref for pref in EstafaNotificationPreference.objects.filter(user__in=users)}
    return {user.pk: saved.get(user.pk) or EstafaNotificationPreference(user=user) for user in users}


def _names_sentence(names):
    names = list(names)
    if len(names) <= 1:
        return ''.join(names)
    return f"{', '.join(names[:-1])} y {names[-1]}"


def record_change(artwork, section, actor, fields=()):
    """Anota un cambio en el historial de la propuesta y, si lo hizo su equipo, en el resumen para ESTAFA.

    Lo que hace ESTAFA al coordinar queda en el historial pero no se avisa por email.
    """
    if actor and actor.is_authenticated:
        history.log_edit(artwork, section, actor, fields)
    if not actor or not actor.is_authenticated or not artwork.can_edit(actor):
        return None
    if not is_fuego_austral(artwork.event):
        return None
    return ArtworkChange.objects.create(artwork=artwork, section=section, actor=actor)


def send_new_artwork_email(artwork):
    """Avisa a ESTAFA que se inscribió una obra, a cada quien por separado."""
    if not is_fuego_austral(artwork.event):
        return 0
    members = list(estafa_members().exclude(email='').exclude(pk=artwork.owner_id).select_related('profile'))
    prefs = _preferences(members)
    context = {
        'artwork': artwork,
        'owner_name': display_name(artwork.owner),
        'review_path': reverse('artwork_review', args=[artwork.event.slug, artwork.pk]),
        'prefs_path': _prefs_path(),
    }
    recipients = [member.email for member in members if prefs[member.pk].new_artworks]
    if not recipients:
        return 0
    sent = 0
    with get_connection() as connection:
        for email in recipients:
            try:
                _send_one(connection, 'art_estafa_new_artwork', email, context)
                sent += 1
            except Exception:
                logging.exception('No se pudo avisar a %s la obra nueva %s', email, artwork.pk)
    return sent


def _lines_for(member, pref, changes):
    """Una línea por obra con lo que cambió, sin los cambios que hizo la misma persona."""
    if not member.email or not (pref.all_changes or pref.assigned_changes):
        return []
    by_artwork = OrderedDict()
    for change in changes:
        artwork = change.artwork
        if change.actor_id == member.pk:
            continue
        if not pref.all_changes and artwork.estafa_contact_id != member.pk:
            continue
        line = by_artwork.setdefault(artwork.pk, {
            'artwork': artwork, 'sections': [], 'actors': [], 'change_ids': [],
            'review_path': reverse('artwork_review', args=[artwork.event.slug, artwork.pk]),
        })
        line['change_ids'].append(change.pk)
        if change.section not in line['sections']:
            line['sections'].append(change.section)
        name = display_name(change.actor) if change.actor else 'alguien del equipo'
        if name not in line['actors']:
            line['actors'].append(name)
    for line in by_artwork.values():
        line['actors_sentence'] = _names_sentence(line['actors'])
    return list(by_artwork.values())


def _reserve(member, lines):
    """Una reserva por persona y conjunto de cambios: si el cron corre dos veces, el email sale una."""
    change_ids = sorted(pk for line in lines for pk in line['change_ids'])
    key = f"art-digest:{member.pk}:{','.join(map(str, change_ids))}"
    reservation, created = MessageIdempotency.objects.get_or_create(
        hash=hashlib.sha256(key.encode()).hexdigest(),
        defaults={'email': member.email, 'payload': {'key': key}},
    )
    return reservation if created else None


def send_estafa_change_digest(event=None, context=None):
    """Cron horario: un email por persona de ESTAFA con las obras que cambiaron."""
    sent = 0
    with transaction.atomic():
        changes = list(
            ArtworkChange.objects.select_for_update(skip_locked=True, of=('self',))
            .filter(digested_at__isnull=True)
            .select_related('artwork__event', 'actor__profile')
            .order_by('created_at')
        )
        if not changes:
            return 0
        members = list(estafa_members().exclude(email='').select_related('profile'))
        prefs = _preferences(members)
        prefs_path = _prefs_path()
        digests = [(member, _lines_for(member, prefs[member.pk], changes)) for member in members]
        digests = [(member, lines) for member, lines in digests if lines]
        if digests:
            with get_connection() as connection:
                for member, lines in digests:
                    reservation = _reserve(member, lines)
                    if not reservation:
                        continue
                    try:
                        _send_one(connection, 'art_estafa_change_digest', member.email, {
                            'lines': lines, 'prefs_path': prefs_path,
                        })
                        sent += 1
                    except Exception:
                        reservation.delete()
                        logging.exception('No se pudo enviar el resumen de cambios de Arte a %s', member.email)
        ArtworkChange.objects.filter(pk__in=[change.pk for change in changes]).update(digested_at=timezone.now())
    return sent
