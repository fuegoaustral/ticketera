"""Emails de las obras para ESTAFA, Becas y MAD: avisos al momento y un resumen horario de cambios.

ESTAFA recibe lo de todas las obras, Becas sólo lo de las becas y MAD sólo lo de las obras que piden MAD.

Cuentan como cambio sólo los que hace el equipo de la instalación (editar la propuesta, la beca,
el checkout, subir fotos o archivos). Lo que hace ESTAFA al coordinar no se avisa.
"""
import hashlib
import logging
from collections import OrderedDict

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import get_connection
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from tickets.models import MessageIdempotency
from user_profile.display import display_name
from templated_email import get_templated_mail

from . import history
from .estafa import (
    BECAS_SLUG, ESTAFA_SLUG, MAD_SLUG, art_team_members, art_team_names, is_fuego_austral,
)
from .models import ArtworkChange, EstafaNotificationPreference

# Las secciones del historial que son de la beca: lo único que le llega a Becas, y lo que ESTAFA puede apagar.
GRANT_REQUEST_SECTION, GRANT_REPORT_SECTION = 'Beca', 'Rendición de beca'
GRANT_SECTIONS = frozenset((GRANT_REQUEST_SECTION, GRANT_REPORT_SECTION))


def _send_one(connection, template_name, email, context):
    """Un email a una sola persona, por una conexión SMTP compartida (muchos envíos seguidos)."""
    message = get_templated_mail(
        template_name=template_name, context={**context, 'base_url': settings.APP_URL},
        from_email=settings.DEFAULT_FROM_EMAIL, to=[email],
    )
    connection.send_messages([message])


def _mad_path(artwork):
    return reverse('artwork_mad', args=[artwork.event.slug, artwork.pk])


def _prefs_path():
    return f"{reverse('profile')}#emails-estafa"


def _preferences(users):
    saved = {pref.user_id: pref for pref in EstafaNotificationPreference.objects.filter(user__in=users)}
    return {user.pk: saved.get(user.pk) or EstafaNotificationPreference(user=user) for user in users}


def _team_members():
    """La gente de ESTAFA, Becas y MAD con email, sus equipos y sus preferencias."""
    teams = art_team_members()
    members = list(User.objects.filter(pk__in=teams).exclude(email='').select_related('profile'))
    return members, teams, _preferences(members)


def _team_label(slugs):
    return _names_sentence(art_team_names(slugs))


def _send_each(template_name, recipients, context, what):
    """El mismo aviso a cada persona por separado. `recipients`: [(persona, etiqueta de su equipo)]."""
    sent = 0
    if not recipients:
        return sent
    with get_connection() as connection:
        for member, team_label in recipients:
            try:
                _send_one(connection, template_name, member.email, {**context, 'team_label': team_label})
                sent += 1
            except Exception:
                logging.exception('No se pudo avisar a %s %s', member.email, what)
    return sent


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
    """Avisa que se inscribió una obra: a ESTAFA siempre y a MAD si la obra pide MAD. Un email por persona."""
    if not is_fuego_austral(artwork.event):
        return 0
    members, teams, prefs = _team_members()
    estafa, mad = [], []
    for member in members:
        if member.pk == artwork.owner_id:
            continue
        slugs, pref = teams[member.pk], prefs[member.pk]
        if ESTAFA_SLUG in slugs and pref.new_artworks:
            estafa.append((member, _team_label(slugs)))
        elif MAD_SLUG in slugs and pref.mad_new_artworks and artwork.asks_mad:
            mad.append((member, _team_label(slugs)))
    context = {
        'artwork': artwork,
        'owner_name': display_name(artwork.owner),
        'review_path': reverse('artwork_review', args=[artwork.event.slug, artwork.pk]),
        'prefs_path': _prefs_path(),
    }
    what = f'la obra nueva {artwork.pk}'
    return (
        _send_each('art_estafa_new_artwork', estafa, context, what)
        + _send_each('art_mad_artwork', mad, {**context, 'review_path': _mad_path(artwork), 'is_new': True}, what)
    )


def send_mad_request_email(artwork, actor=None):
    """Una obra ya inscripta pasó a pedir MAD: se le avisa a MAD."""
    if not is_fuego_austral(artwork.event):
        return 0
    members, teams, prefs = _team_members()
    recipients = [
        (member, _team_label(teams[member.pk])) for member in members
        if MAD_SLUG in teams[member.pk] and prefs[member.pk].mad_new_artworks
        and member.pk != getattr(actor, 'pk', None)
    ]
    return _send_each('art_mad_artwork', recipients, {
        'artwork': artwork,
        'owner_name': display_name(artwork.owner),
        'review_path': _mad_path(artwork),
        'prefs_path': _prefs_path(),
        'is_new': False,
    }, f'que la obra {artwork.pk} pide MAD')


def send_grant_application_email(artwork, actor=None):
    """Un equipo presentó su solicitud de beca: se le avisa a Becas."""
    if not is_fuego_austral(artwork.event):
        return 0
    members, teams, prefs = _team_members()
    recipients = [
        (member, _team_label(teams[member.pk])) for member in members
        if BECAS_SLUG in teams[member.pk] and prefs[member.pk].becas_new_applications
        and member.pk != getattr(actor, 'pk', None)
    ]
    return _send_each('art_grant_application', recipients, {
        'artwork': artwork,
        'actor_name': display_name(actor) if actor else display_name(artwork.owner),
        'grant_path': reverse('artwork_grant', args=[artwork.pk]),
        'prefs_path': _prefs_path(),
    }, f'la solicitud de beca de la obra {artwork.pk}')


def _wants_change(member, slugs, pref, change):
    """Si a esta persona le corresponde enterarse del cambio, por alguno de sus equipos."""
    artwork, is_grant = change.artwork, change.section in GRANT_SECTIONS
    if ESTAFA_SLUG in slugs and (pref.grant_updates or not is_grant):
        if pref.all_changes or (pref.assigned_changes and artwork.estafa_contact_id == member.pk):
            return True
    if BECAS_SLUG in slugs and is_grant and pref.becas_changes:
        return True
    return MAD_SLUG in slugs and pref.mad_changes and artwork.asks_mad


def _lines_for(member, slugs, pref, changes):
    """Una línea por obra con lo que cambió, sin los cambios que hizo la misma persona."""
    if not member.email:
        return []
    by_artwork = OrderedDict()
    for change in changes:
        artwork = change.artwork
        if change.actor_id == member.pk or not _wants_change(member, slugs, pref, change):
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
    """Cron horario: un email por persona de ESTAFA, Becas y MAD con las obras que cambiaron."""
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
        members, teams, prefs = _team_members()
        prefs_path = _prefs_path()
        digests = [(member, _lines_for(member, teams[member.pk], prefs[member.pk], changes)) for member in members]
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
                            'team_label': _team_label(teams[member.pk]),
                        })
                        sent += 1
                    except Exception:
                        reservation.delete()
                        logging.exception('No se pudo enviar el resumen de cambios de Arte a %s', member.email)
        ArtworkChange.objects.filter(pk__in=[change.pk for change in changes]).update(digested_at=timezone.now())
    return sent
