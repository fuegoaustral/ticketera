"""Los equipos globales que trabajan sobre Arte. Sólo actúan sobre eventos de Fuego Austral.

ESTAFA coordina todo. Becas ve todas las propuestas y gestiona sus becas. MAD ve todas las
propuestas, sin editarlas.
"""
from django.contrib.auth.models import User

from events.models import Event
from teams.models import TeamMembership

ESTAFA_SLUG, BECAS_SLUG, MAD_SLUG = 'estafa', 'becas', 'mad'
# En orden de alcance: quien está en más de un equipo se presenta con el primero.
ART_TEAMS = ((ESTAFA_SLUG, 'ESTAFA'), (BECAS_SLUG, 'Becas'), (MAD_SLUG, 'MAD'))


def estafa_members():
    active = TeamMembership.objects.active().filter(team__slug=ESTAFA_SLUG)
    return User.objects.filter(pk__in=active.values('user_id'))


def art_team_members():
    """Quién está activo en algún equipo de Arte: {user_id: {slugs}}."""
    teams = {}
    rows = TeamMembership.objects.active().filter(team__slug__in=[slug for slug, _name in ART_TEAMS])
    for user_id, slug in rows.values_list('user_id', 'team__slug'):
        teams.setdefault(user_id, set()).add(slug)
    return teams


def art_team_names(slugs):
    return [name for slug, name in ART_TEAMS if slug in slugs]


def art_teams(user):
    """Los equipos de Arte en los que la persona está activa."""
    if not user.is_authenticated:
        return frozenset()
    if not hasattr(user, '_art_teams'):
        user._art_teams = frozenset(TeamMembership.objects.active().filter(
            team__slug__in=[slug for slug, _name in ART_TEAMS], user=user,
        ).values_list('team__slug', flat=True))
    return user._art_teams


def is_estafa_member(user):
    return ESTAFA_SLUG in art_teams(user)


def art_team_name(user):
    """Con qué equipo entra a las propuestas de Arte ('' si no entra)."""
    teams = art_teams(user)
    if user.is_authenticated and user.is_superuser and ESTAFA_SLUG not in teams:
        return 'ESTAFA'
    return next((name for slug, name in ART_TEAMS if slug in teams), '')


def can_access_estafa(user):
    return user.is_authenticated and (user.is_superuser or bool(art_teams(user)))


def is_fuego_austral(event):
    # Los eventos de Fuego Austral son los que tienen voluntariado (Rangers, Transmutadores, CAOS).
    return event.has_volunteers


def estafa_events(user):
    events = Event.objects.filter(art_program__isnull=False).order_by('-start')
    if user.is_superuser:
        return events
    if art_teams(user):
        return events.filter(has_volunteers=True)
    return events.none()


def _in_teams(user, event, slugs):
    return user.is_authenticated and (
        user.is_superuser or (is_fuego_austral(event) and bool(art_teams(user) & slugs))
    )


def can_coordinate(user, event):
    """Editar y decidir sobre las propuestas: ESTAFA."""
    return _in_teams(user, event, {ESTAFA_SLUG})


def can_manage_grants(user, event):
    """Gestionar las becas: Becas y ESTAFA."""
    return _in_teams(user, event, {ESTAFA_SLUG, BECAS_SLUG})


def can_message_teams(user, event):
    """Escribirle al equipo de una propuesta: ESTAFA, Becas y MAD."""
    return _in_teams(user, event, {ESTAFA_SLUG, BECAS_SLUG, MAD_SLUG})


def can_view_art(user, event):
    """Ver todas las propuestas: ESTAFA, Becas y MAD."""
    return _in_teams(user, event, {ESTAFA_SLUG, BECAS_SLUG, MAD_SLUG})
