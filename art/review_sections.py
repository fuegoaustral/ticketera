"""Las secciones de una instalación vistas por ESTAFA: estado, fecha y contenido para leer.

Qué falta en cada sección sigue los mismos campos obligatorios que ve el equipo.
"""
from dataclasses import dataclass, field

from django.utils import timezone
from django.utils.formats import date_format

from .models import Artwork
from .templatetags.art_format import person_label

COMPLETE, MISSING, INFO, UPCOMING, ESTAFA_TURN = 'complete', 'missing', 'info', 'upcoming', 'estafa'

# Campos que completa ESTAFA en cada sección.
ESTAFA_FIELDS = {
    'declaracion': (
        'checkin_arrived_at', 'checkin_art_at', 'checkin_placed',
        'checkin_placement_changed', 'checkin_placement_change_notes',
        'understanding_letter', 'understanding_letter_physical_received',
        'understanding_letter_physical_custodian', 'understanding_letter_physical_notes',
        'understanding_letter_physical_waiver', 'understanding_letter_physical_waiver_reason',
    ),
    'desplegable': ('assigned_location', 'placement_notes'),
    'checkout': ('checkout_team_responsible', 'checkout_verified_at'),
    'beneficio': ('benefit_status', 'benefit_notes'),
}


@dataclass
class Section:
    key: str
    title: str
    anchor: str
    status: str
    summary: str
    when: str = ''
    missing: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    estafa_fields: tuple = ()


def _day(value):
    return date_format(timezone.localtime(value) if hasattr(value, 'tzinfo') else value, 'd/m')


def _when(program, blocks, deadline, at):
    states = {program.checkpoint_state(block, at) for block in blocks}
    if 'open' in states:
        return f'Hasta el {_day(deadline)}' if deadline else 'Abierta'
    if states == {'upcoming'}:
        return 'Todavía no abre'
    return f'Cerró el {_day(deadline)}' if deadline else 'Cerrada'


def _rows(artwork, names):
    rows = []
    for name in names:
        model_field = Artwork._meta.get_field(name)
        value = getattr(artwork, name)
        if isinstance(value, bool):
            value = 'Sí' if value else 'No'
        elif hasattr(value, 'tzinfo'):
            value = date_format(timezone.localtime(value), 'd/m/Y H:i') if value else value
        elif model_field.choices:
            value = getattr(artwork, f'get_{name}_display')()
        rows.append((model_field.verbose_name, value))
    return rows


def _status(missing, complete_summary='Completo'):
    if missing:
        return MISSING, 'Pendiente: ' + ', '.join(label.lower() for label in missing)
    return COMPLETE, complete_summary


def review_sections(artwork, program, at=None):
    at = at or timezone.now()
    sections = []

    # Propuesta y seguridad
    missing = [label for name, label in (
        ('proposal', 'Descripción'), ('dimensions', 'Dimensiones'), ('materials', 'Materiales'),
    ) if not getattr(artwork, name)]
    if artwork.uses_fire:
        missing += [label for name, label in (
            ('fire_details', 'Detalles del fuego'), ('extinguishing_plan', 'Plan de extinción'),
            ('safety_responsible_id', 'Responsable de seguridad'),
        ) if not getattr(artwork, name)]
    status, summary = _status(missing)
    rows = _rows(artwork, (
        'proposal', 'dimensions', 'materials', 'technical_needs', 'power_watts', 'uses_sound',
        'uses_fire', *(('fire_details', 'extinguishing_plan') if artwork.uses_fire else ()), 'safety_plan',
    ))
    rows.append(('Responsable de seguridad', person_label(artwork.safety_responsible)))
    sections.append(Section(
        'propuesta', 'Propuesta y seguridad', 'propuesta', status, summary,
        _when(program, ('proposal',), program.proposal_deadline, at), missing, rows,
    ))

    # Equipo de la instalación
    members = list(artwork.team_members())
    editors = set(artwork.collaborators.values_list('pk', flat=True))
    for member in members:
        member.role = 'Responsable' if member.user_id == artwork.owner_id else ('Puede editar' if member.user_id in editors else 'Ve la instalación')
    members.sort(key=lambda member: member.user_id != artwork.owner_id)
    count = len(members)
    sections.append(Section(
        'equipo', 'Equipo de la instalación', 'equipo', INFO,
        f'{count} persona{"s" if count != 1 else ""}', 'Sin fecha límite', rows=members,
    ))

    # Galería
    photos = list(artwork.photos.all())
    sections.append(Section(
        'galeria', 'Galería', 'galeria', INFO,
        f'{len(photos)} foto{"s" if len(photos) != 1 else ""}' if photos else 'Sin fotos', 'Opcional', rows=photos,
    ))

    # Desplegable y placement
    missing = [label for name, label in (
        ('public_title', 'Título para el público'), ('public_description', 'Texto para el público'),
    ) if not getattr(artwork, name)]
    status, summary = _status(missing)
    placement = f'Ubicación: {artwork.assigned_location}' if artwork.assigned_location else 'Sin ubicación asignada'
    sections.append(Section(
        'desplegable', 'Desplegable y placement', 'placement', status, f'{summary} · {placement}',
        _when(program, ('guide',), program.guide_deadline, at), missing,
        _rows(artwork, ('public_title', 'public_description', 'preferred_location')),
        ESTAFA_FIELDS['desplegable'],
    ))

    # Ingreso anticipado, proveedores y desarme
    providers = list(artwork.artwork_providers.prefetch_related('vehicles'))
    parts = []
    if artwork.arrival_date:
        parts.append(f'Ingreso {_day(artwork.arrival_date)}')
    if providers:
        parts.append(f'{len(providers)} proveedor{"es" if len(providers) != 1 else ""}')
    sections.append(Section(
        'logistica', 'Ingreso anticipado, proveedores y desarme', 'logistica', INFO,
        ' · '.join(parts) or 'Sin datos cargados', _when(program, ('logistics',), program.logistics_deadline, at),
        rows=providers,
    ))

    # Check-in y declaración de entendimiento
    missing = []
    if not artwork.understanding_letter:
        missing.append('Declaración digital')
    if not artwork.understanding_letter_physical_received and not artwork.understanding_letter_physical_waiver:
        missing.append('Declaración física')
    status, summary = _status(missing)
    deadlines = [d for d in (program.understanding_letter_digital_deadline, program.understanding_letter_physical_deadline) if d]
    sections.append(Section(
        'declaracion', 'Check-in y declaración de entendimiento', 'carta-entendimiento', status, summary,
        _when(program, ('understanding_letter_digital', 'understanding_letter_physical'), max(deadlines) if deadlines else None, at),
        missing, estafa_fields=ESTAFA_FIELDS['declaracion'],
    ))

    # Checkout
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
        _when(program, ('checkout',), program.checkout_deadline, at), missing,
        _rows(artwork, ('checkout_notes', 'checkout_requested_at')),
        ESTAFA_FIELDS['checkout'],
    ))

    # Beneficio para la próxima edición: lo decide ESTAFA.
    sections.append(Section(
        'beneficio', 'Beneficio para la próxima edición', '', INFO, artwork.get_benefit_status_display(),
        estafa_fields=ESTAFA_FIELDS['beneficio'],
    ))
    # El mismo orden que la página de la instalación.
    order = ('declaracion', 'propuesta', 'equipo', 'galeria', 'desplegable', 'logistica', 'checkout', 'beneficio')
    return sorted(sections, key=lambda section: order.index(section.key))
