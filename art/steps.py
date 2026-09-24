"""Los pasos de una instalación: estado, fecha a mostrar y qué falta.

Una sola fuente para la línea de tiempo, "Lo próximo" y cualquier otro resumen,
así nunca se contradicen.
"""
from dataclasses import dataclass, field

from django.utils import timezone
from django.utils.formats import date_format

from .models import Artwork

OPEN, CLOSED, UPCOMING = 'open', 'closed', 'upcoming'


@dataclass
class Step:
    key: str
    title: str
    short: str
    state: str
    when: str
    deadline: object = None
    missing: list = field(default_factory=list)
    # Los pasos opcionales no muestran "Completo" ni "Te falta".
    optional: bool = False
    about: str = ''

    @property
    def complete(self):
        return self.state == OPEN and not self.optional and not self.missing

    @property
    def missing_count(self):
        """'Falta 1 campo' / 'Faltan 3 pasos': Declaración y Checkout son pasos a hacer, el resto son campos."""
        count = len(self.missing)
        unit = 'paso' if self.key in ('carta', 'checkout') else 'campo'
        return f"{'Falta' if count == 1 else 'Faltan'} {count} {unit}{'' if count == 1 else 's'}"

    @property
    def template(self):
        return f'art/steps/{self.key}.html'


# key, título, título corto, bloque de fechas del programa (None: sin fechas), opcional, qué se hace
STEPS = (
    ('detalles', 'Detalles de la instalación', 'Detalles', 'proposal', False,
     'Contás qué vas a hacer, con qué materiales y cómo la vas a cuidar.'),
    ('equipo', 'Equipo', 'Equipo', None, True,
     'Sumás a quienes hacen la instalación con vos.'),
    ('desplegable', 'Desplegable y placement', 'Desplegable', 'guide', False,
     'Escribís el texto que lee el público y elegís dónde te gustaría ubicarte.'),
    ('carta', 'Declaración de entendimiento', 'Declaración', 'understanding_letter', False,
     'Firmás la declaración, subís una foto o un PDF y entregás la copia física.'),
    ('ingreso', 'Ingreso anticipado, late checkout y proveedores', 'Ingreso y proveedores', 'logistics', True,
     'Cargás quién entra antes y quién se queda después, y qué proveedores y vehículos vienen.'),
    ('galeria', 'Galería', 'Galería', 'gallery', True,
     'Subís fotos del armado y de la instalación terminada. Es opcional.'),
    ('checkout', 'Checkout', 'Checkout', 'checkout', False,
     'Cuando desarmás y limpiás, subís fotos del lugar y avisás que terminaste.'),
)


def _day(value):
    return date_format(timezone.localtime(value) if hasattr(value, 'tzinfo') else value, 'd/m')


def _letter_state(program, at):
    """La declaración tiene dos plazos: digital y copia física. Está abierta mientras alguno lo esté."""
    states = {program.checkpoint_state(block, at) for block in ('understanding_letter_digital', 'understanding_letter_physical')}
    if OPEN in states:
        return OPEN
    return UPCOMING if states == {UPCOMING} else CLOSED


def _letter_deadline(program):
    deadlines = [program.deadline_for(block) for block in ('understanding_letter_digital', 'understanding_letter_physical')]
    deadlines = [deadline for deadline in deadlines if deadline]
    return max(deadlines) if deadlines else None


def _missing(key, artwork):
    if key == 'detalles':
        missing = [label for name, label in (
            ('title', 'Nombre'), ('proposal', 'Descripción'), ('dimensions', 'Dimensiones'), ('materials', 'Materiales'),
        ) if not getattr(artwork, name)]
        if artwork.uses_fire:
            missing += [label for name, label in (
                ('fire_details', 'Detalles del fuego'), ('extinguishing_plan', 'Plan de extinción'),
                ('safety_responsible_id', 'Responsable de seguridad'),
            ) if not getattr(artwork, name)]
        if artwork.burns:
            missing += [label for name, label in (
                ('burn_preferred_time', 'Cuándo preferís quemarla'), ('burn_company', 'Si la quemás sola o con otras'),
            ) if not getattr(artwork, name)]
        return missing
    if key == 'desplegable':
        return [label for name, label in (
            ('public_title', 'Título para el público'), ('public_description', 'Texto para el público'),
        ) if not getattr(artwork, name)]
    if key == 'carta':
        missing = []
        if not artwork.understanding_letter:
            missing.append('Subir la declaración firmada')
        if not artwork.understanding_letter_physical_received and not artwork.understanding_letter_physical_waiver:
            missing.append('Entregar la copia física')
        return missing
    if key == 'checkout':
        if artwork.checkout_completed or artwork.status != Artwork.Status.ACTIVE:
            return []
        return ['Fotos del espacio'] if not artwork.checkout_photos.exists() else ['Enviar el checkout']
    return []


# Qué paso muestra cada bloque de campos del formulario principal.
BLOCK_STEPS = {
    'proposal': 'detalles', 'guide': 'desplegable', 'logistics': 'ingreso',
    'checkout': 'checkout', 'understanding_letter_digital': 'carta',
}


def _error_labels(form):
    """Campos con error en un formulario que no se guardó, por paso: cuentan como pendientes."""
    labels = {}
    if not form or not form.is_bound:
        return labels
    for block, names in form.BLOCK_FIELDS.items():
        for name in names:
            if name in form.errors and name in form.fields:
                labels.setdefault(BLOCK_STEPS.get(block), []).append(form.fields[name].label)
    return labels


def artwork_steps(artwork, program, at=None, form=None):
    """Pasos en el orden de la instalación. Sin instalación guardada, sólo Detalles está abierto.

    Con un formulario que volvió con errores, el estado refleja lo que se ve en pantalla: esos
    campos cuentan como pendientes aunque lo guardado esté completo.
    """
    errors = _error_labels(form)
    at = at or timezone.now()
    read_only = not program.is_current or (artwork and artwork.status == Artwork.Status.REJECTED)
    steps = []
    for key, title, short, block, optional, about in STEPS:
        if block == 'understanding_letter':
            # La declaración no tiene apertura: sólo cierres.
            state, deadline, opens = _letter_state(program, at), _letter_deadline(program), None
        elif block:
            state, deadline, opens = program.checkpoint_state(block, at), program.deadline_for(block), program.opens_at(block)
        else:
            # El equipo se puede armar durante toda la edición.
            state, deadline, opens = OPEN, None, None
        if key == 'detalles' and not artwork:
            state = OPEN
        if read_only:
            state = CLOSED

        if not artwork and key != 'detalles':
            state, when = UPCOMING, 'Después de guardar'
        elif state == UPCOMING:
            # La fecha de ingreso anticipado no se anuncia antes de que se habilite.
            when = 'Te avisamos cuando se habilite' if key == 'ingreso' or not opens else f'Desde el {_day(opens)}'
        elif state == CLOSED:
            when = f'Cerró el {_day(deadline)}' if deadline else 'Cerrado'
        else:
            when = f'Abierto · hasta el {_day(deadline)}' if deadline else 'Abierto'

        if artwork and state == OPEN:
            missing = _missing(key, artwork)
        elif key == 'detalles' and form is not None:
            # Instalación nueva: lo que falta sale de lo que se está cargando.
            missing = _missing(key, form.instance)
        else:
            missing = []
        if state == OPEN:
            missing = list(dict.fromkeys(missing + errors.get(key, [])))
        steps.append(Step(key, title, short, state, when, deadline, missing, optional, about))
    return steps


def next_step(steps):
    """Lo próximo: el paso abierto con algo pendiente que vence primero."""
    pending = [step for step in steps if step.state == OPEN and step.missing and not step.optional]
    if not pending:
        return None
    return min(pending, key=lambda step: (step.deadline is None, step.deadline or 0))
