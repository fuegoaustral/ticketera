"""Las secciones de una instalación vistas por ESTAFA: estado, fecha y qué se lee en cada una.

Siguen el orden de la línea de tiempo del equipo. Qué falta en cada sección sale de los mismos
campos obligatorios que ve el equipo (steps._missing) y el contenido se lee con los mismos
resúmenes de cada paso (art/steps/summary/).
"""
from dataclasses import dataclass, field

from django.utils import timezone
from django.utils.formats import date_format

from .models import Artwork, ArtworkPhoto
from .steps import _missing

COMPLETE, MISSING, INFO, UPCOMING, ESTAFA_TURN = 'complete', 'missing', 'info', 'upcoming', 'estafa'

# Campos que completa ESTAFA en cada sección. La declaración digital la sube sólo el equipo.
ESTAFA_FIELDS = {
    'declaracion': (
        'understanding_letter_physical_received', 'understanding_letter_physical_received_at',
        'understanding_letter_physical_custodian', 'understanding_letter_physical_notes',
        'understanding_letter_physical_waiver', 'understanding_letter_physical_waiver_reason',
    ),
    'checkin': (
        'checkin_arrived_at', 'checkin_art_at', 'checkin_placed',
        'checkin_placement_changed', 'checkin_placement_change_notes',
    ),
    # El beneficio para la próxima edición se decide al cerrar: después de verificar el checkout.
    'checkout': (
        'checkout_team_responsible', 'checkout_verified_at', 'checkout_staff_notes',
        'benefit_status', 'benefit_notes',
    ),
}

SUMMARY = 'art/steps/summary/'


@dataclass
class Section:
    key: str
    title: str
    # El paso de la instalación donde el equipo ve lo mismo ('' si no hay).
    anchor: str
    status: str
    summary: str
    when: str = ''
    missing: list = field(default_factory=list)
    estafa_fields: tuple = ()
    # Lo que cargó el equipo, para leer.
    template: str = ''


def _day(value):
    return date_format(timezone.localtime(value) if hasattr(value, 'tzinfo') else value, 'd/m')


def _when(program, blocks, deadline, at):
    states = {program.checkpoint_state(block, at) for block in blocks}
    if 'open' in states:
        return f'Hasta el {_day(deadline)}' if deadline else 'Abierta'
    if states == {'upcoming'}:
        return 'Todavía no abre'
    return f'Cerró el {_day(deadline)}' if deadline else 'Cerrada'


def _status(missing, complete_summary='Completo'):
    if missing:
        return MISSING, 'Pendiente: ' + ', '.join(label.lower() for label in missing)
    return COMPLETE, complete_summary


def _count(number, singular, plural):
    return f'{number} {singular if number == 1 else plural}'


def review_sections(artwork, program, at=None):
    at = at or timezone.now()
    sections = []

    # Detalles, con la seguridad y el fuego: los mismos campos y en el mismo orden que ve el equipo.
    missing = _missing('detalles', artwork)
    status, summary = _status(missing)
    sections.append(Section(
        'detalles', 'Detalles de la instalación', 'detalles', status, summary,
        _when(program, ('proposal',), program.proposal_deadline, at), missing, template=SUMMARY + 'detalles.html',
    ))

    members = list(artwork.team_members())
    sections.append(Section(
        'equipo', 'Equipo', 'equipo', INFO, _count(len(members), 'persona', 'personas'), 'Sin fecha límite',
        template=SUMMARY + 'equipo.html',
    ))

    missing = _missing('desplegable', artwork)
    status, summary = _status(missing)
    sections.append(Section(
        'desplegable', 'Desplegable y placement', 'desplegable', status, summary,
        _when(program, ('guide',), program.guide_deadline, at), missing, template=SUMMARY + 'desplegable.html',
    ))

    missing = []
    if not artwork.understanding_letter:
        missing.append('Declaración digital')
    if not artwork.understanding_letter_physical_received and not artwork.understanding_letter_physical_waiver:
        missing.append('Declaración física')
    status, summary = _status(missing)
    deadlines = [d for d in (program.understanding_letter_digital_deadline, program.understanding_letter_physical_deadline) if d]
    sections.append(Section(
        'declaracion', 'Declaración de entendimiento', 'carta', status, summary,
        _when(program, ('understanding_letter_digital', 'understanding_letter_physical'), max(deadlines) if deadlines else None, at),
        missing, ESTAFA_FIELDS['declaracion'], SUMMARY + 'carta.html',
    ))

    logistics_when = _when(program, ('logistics',), program.logistics_deadline, at)
    group = artwork.operations_group if artwork.operations_group_id else None
    early = sum(1 for member in members if member.ingreso_anticipado_fecha)
    late = sum(1 for member in members if member.late_checkout)
    sections.append(Section(
        'ingreso', 'Ingreso anticipado', 'ingreso', INFO,
        f'{early} de {group.ingreso_anticipado_amount} cupos' if group and group.ingreso_anticipado_amount else 'Sin cupos',
        logistics_when, template=SUMMARY + '_early_entry.html',
    ))
    sections.append(Section(
        'late-checkout', 'Late checkout', 'ingreso', INFO,
        f'{late} de {group.late_checkout_amount} cupos' if group and group.late_checkout_amount else 'Sin cupos',
        logistics_when, template=SUMMARY + '_late_checkout.html',
    ))
    providers = artwork.artwork_providers.count()
    sections.append(Section(
        'proveedores', 'Proveedores', 'ingreso', INFO,
        _count(providers, 'proveedor', 'proveedores') if providers else 'Sin proveedores', logistics_when,
        template=SUMMARY + '_providers.html',
    ))

    # Check-in en el predio: lo registra ESTAFA cuando llega la instalación.
    if artwork.checkin_art_at:
        status, summary = COMPLETE, f'Hecho el {date_format(timezone.localtime(artwork.checkin_art_at), "d/m H:i")}'
    elif artwork.checkin_arrived_at:
        status, summary = ESTAFA_TURN, 'Llegó · falta el check-in con Arte'
    else:
        status, summary = INFO, 'Sin registrar'
    sections.append(Section(
        'checkin', 'Check-in', '', status, summary, 'En el evento', estafa_fields=ESTAFA_FIELDS['checkin'],
    ))

    stage = artwork.stage
    missing = []
    if stage == Artwork.CHECKOUT_PENDING:
        missing = ['Fotos del espacio'] if not artwork.checkout_photos.exists() else ['Enviar el checkout']
        status, summary = MISSING, 'Pendiente: ' + missing[0].lower()
    elif stage == Artwork.Status.CHECKOUT_SUBMITTED:
        status, summary = ESTAFA_TURN, 'Enviado · falta verificar'
    elif stage == Artwork.Status.CHECKOUT_VERIFIED:
        status, summary = COMPLETE, 'Verificado'
    else:
        status, summary = UPCOMING, 'Todavía no corresponde'
    sections.append(Section(
        'checkout', 'Checkout', 'checkout', status, summary,
        _when(program, ('checkout',), program.checkout_deadline, at), missing, ESTAFA_FIELDS['checkout'],
        'art/review/_checkout.html',
    ))

    # Galería: las fotos del armado y de la instalación terminada, como la ve el equipo.
    photos = artwork.photos.filter(stage__in=(ArtworkPhoto.Stage.PROCESS, ArtworkPhoto.Stage.FINAL)).count()
    sections.append(Section(
        'galeria', 'Galería', 'galeria', INFO, _count(photos, 'foto', 'fotos') if photos else 'Sin fotos', 'Opcional',
        template=SUMMARY + 'galeria.html',
    ))
    return sections
