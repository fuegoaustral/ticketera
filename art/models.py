from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from auditlog.registry import auditlog

from events.models import Event
from utils.models import BaseModel

from .estafa import can_coordinate


def default_art_reminder_days():
    return [7, 3, 1]


def private_art_storage():
    if getattr(settings, 'DEFAULT_FILE_STORAGE', '') == 'django_s3_storage.storage.S3Storage':
        from django_s3_storage.storage import S3Storage
        return S3Storage(aws_s3_bucket_auth=True, aws_s3_key_prefix='private')
    return default_storage


# Se copia al equipo de cada instalación cuando se crea (ver _ensure_operations_group).
TEAM_COPY_HELP = 'Se copia al equipo de cada instalación cuando se crea. Cambiarlo no modifica los equipos que ya existen.'


class ArtProgram(BaseModel):
    """Fechas que habilitan y bloquean cada bloque del formulario de Arte."""

    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name='art_program', verbose_name='Evento')
    is_current = models.BooleanField(
        default=False, verbose_name='Convocatoria anual vigente',
        help_text='La única convocatoria que acepta instalaciones nuevas y que los equipos pueden editar. '
                  'Las demás quedan como histórico, en modo consulta.',
    )
    registration_opens = models.DateTimeField(
        null=True, blank=True, verbose_name='Apertura de inscripción',
        help_text='Desde cuándo se pueden inscribir instalaciones nuevas (botón «Nueva instalación»). '
                  'Vacía: la inscripción no se habilita.',
    )
    registration_closes = models.DateTimeField(
        null=True, blank=True, verbose_name='Cierre de inscripción',
        help_text='Hasta cuándo se pueden inscribir instalaciones nuevas. Las ya inscriptas se siguen completando '
                  'según las fechas de cada paso. Vacía: no cierra.',
    )
    proposal_deadline = models.DateTimeField(
        null=True, blank=True, verbose_name='Cierre de detalles',
        help_text='Último momento para editar los detalles de la instalación: descripción, dimensiones, materiales y '
                  'seguridad. Puede ser posterior al cierre de inscripción. Vacía: no cierra.',
    )
    grants_enabled = models.BooleanField(
        default=False, verbose_name='Becas habilitadas', help_text='Muestra el paso de beca a los equipos.',
    )
    grant_opens = models.DateTimeField(
        null=True, blank=True, verbose_name='Apertura de becas',
        help_text='Desde cuándo se puede pedir beca. Vacía: el paso no se habilita.',
    )
    grant_deadline = models.DateTimeField(
        null=True, blank=True, verbose_name='Cierre de becas',
        help_text='Último momento para pedir beca y cargar el presupuesto. Vacía: no cierra.',
    )
    guide_deadline = models.DateTimeField(
        null=True, blank=True, verbose_name='Cierre de desplegable y placement',
        help_text='Último momento para editar el título y el texto para el público y la ubicación preferida. '
                  'Vacía: no cierra.',
    )
    public_description_max_length = models.PositiveIntegerField(
        default=200,
        validators=[MinValueValidator(1), MaxValueValidator(500)],
        verbose_name='Máximo de caracteres de la descripción del desplegable',
    )
    logistics_opens = models.DateTimeField(
        null=True, blank=True, verbose_name='Apertura de logística',
        help_text='Mientras esté vacía, el paso queda cerrado y los equipos ven “Te avisamos cuando se habilite”. '
                  'La fecha nunca se les muestra antes de que se habilite.',
    )
    logistics_deadline = models.DateTimeField(
        null=True, blank=True, verbose_name='Cierre de logística',
        help_text='Último momento para cargar ingreso anticipado, late checkout, proveedores y vehículos. '
                  'Vacía: no cierra.',
    )
    gallery_opens = models.DateTimeField(
        null=True, blank=True, verbose_name='Apertura de galería',
        help_text='Desde cuándo los equipos pueden subir fotos del armado y de la instalación terminada. '
                  'Vacía: el paso no se habilita.',
    )
    gallery_deadline = models.DateTimeField(
        null=True, blank=True, verbose_name='Cierre de galería',
        help_text='Último momento para subir fotos a la galería. Vacía: no cierra.',
    )
    checkout_opens = models.DateTimeField(
        null=True, blank=True, verbose_name='Apertura de checkout',
        help_text='Desde cuándo los equipos pueden enviar el checkout. Vacía: el paso no se habilita.',
    )
    checkout_deadline = models.DateTimeField(
        null=True, blank=True, verbose_name='Cierre de checkout',
        help_text='Último momento para enviar el checkout. Vacía: no cierra.',
    )
    understanding_letter_digital_deadline = models.DateTimeField(
        null=True, blank=True, verbose_name='Cierre de declaración digital',
        help_text='Último momento para subir la declaración firmada. Vacía: no cierra.',
    )
    understanding_letter_physical_deadline = models.DateTimeField(
        null=True, blank=True, verbose_name='Cierre de entrega de la copia física',
        help_text='Último momento para entregar la copia en papel. Vacía: no cierra.',
    )
    grant_report_deadline = models.DateTimeField(
        null=True, blank=True, verbose_name='Cierre de rendición de becas',
        help_text='Último momento para rendir una beca aprobada. Vacía: no cierra.',
    )
    reminder_days = models.JSONField(
        default=default_art_reminder_days, blank=True, verbose_name='Días de anticipación para recordatorios',
        help_text='Cuántos días antes de cada cierre se manda un recordatorio, por ejemplo [7, 3, 1]. '
                  'Una lista vacía los desactiva.',
    )
    reminder_email_enabled = models.BooleanField(default=True, verbose_name='Recordatorios por email')
    reminder_whatsapp_enabled = models.BooleanField(default=False, verbose_name='Recordatorios por WhatsApp')
    early_entry_slots = models.PositiveIntegerField(
        default=0, verbose_name='Cupos de ingreso anticipado por instalación', help_text=TEAM_COPY_HELP,
    )
    early_entry_from = models.DateTimeField(
        null=True, blank=True, verbose_name='Ingreso anticipado desde', help_text=TEAM_COPY_HELP,
    )
    late_checkout_slots = models.PositiveIntegerField(
        default=0, verbose_name='Cupos de late checkout por instalación', help_text=TEAM_COPY_HELP,
    )
    late_checkout_until = models.DateTimeField(
        null=True, blank=True, verbose_name='Late checkout hasta', help_text=TEAM_COPY_HELP,
    )

    class Meta:
        verbose_name = 'Programa de Arte'
        verbose_name_plural = 'Programas de Arte'
        constraints = [
            models.UniqueConstraint(fields=['is_current'], condition=Q(is_current=True), name='unique_current_art_program'),
        ]

    # Apertura y cierre de cada bloque: el cierre no puede ser anterior.
    DATE_PAIRS = (
        ('registration_opens', 'registration_closes'), ('grant_opens', 'grant_deadline'),
        ('logistics_opens', 'logistics_deadline'), ('gallery_opens', 'gallery_deadline'),
        ('checkout_opens', 'checkout_deadline'),
    )

    def __str__(self):
        return f'Arte · {self.event.name}'

    def clean(self):
        errors = {}
        for opens, closes in self.DATE_PAIRS:
            if getattr(self, opens) and getattr(self, closes) and getattr(self, closes) < getattr(self, opens):
                errors[closes] = 'El cierre no puede ser anterior a la apertura.'
        if not isinstance(self.reminder_days, list) or any(not isinstance(day, int) or day < 0 for day in self.reminder_days):
            errors['reminder_days'] = 'Usá una lista de días enteros no negativos, por ejemplo [7, 3, 1].'
        if errors:
            raise ValidationError(errors)

    def registration_is_open(self, at=None):
        return self.checkpoint_state('registration', at) == 'open'

    def checkout_is_open(self, at=None):
        return self.checkpoint_state('checkout', at) != 'upcoming'

    def opens_at(self, block):
        return getattr(self, f'{block}_opens', None)

    def deadline_for(self, block):
        return getattr(self, 'registration_closes' if block == 'registration' else f'{block}_deadline', None)

    def checkpoint_state(self, block, at=None):
        """Los bloques con apertura no se habilitan hasta que ESTAFA pone la fecha. Sin cierre, no cierran.

        Sólo usa fechas del programa: las del evento quedan para los bonos.
        """
        at = at or timezone.now()
        if hasattr(self, f'{block}_opens'):
            opens = self.opens_at(block)
            if not opens or at < opens:
                return 'upcoming'
        deadline = self.deadline_for(block)
        return 'closed' if deadline and at > deadline else 'open'


class Artwork(BaseModel):
    class Kind(models.TextChoices):
        PLANNED = 'planned', 'Instalación inscripta'
        POPUP = 'popup', 'Instalación espontánea (popup)'

    class GrantStatus(models.TextChoices):
        NOT_REQUESTED = 'none', 'No solicitada'
        PENDING = 'pending', 'Pendiente'
        INFO_REQUIRED = 'info', 'Requiere información'
        APPROVED = 'approved', 'Aprobada'
        REJECTED = 'rejected', 'No aprobada'
        PAID = 'paid', 'Pagada'
        REPORTED = 'reported', 'Rendición enviada'
        CLOSED = 'closed', 'Rendición aprobada'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Inscripción pendiente'
        ACTIVE = 'active', 'Inscripción aprobada'
        REJECTED = 'rejected', 'Rechazada'
        CHECKOUT_SUBMITTED = 'checkout', 'Checkout enviado'
        CHECKOUT_VERIFIED = 'verified', 'Checkout verificado'

    # Etapa que se muestra: una inscripción aprobada pasa a "Checkout pendiente"
    # cuando se habilita el checkout, sin que nadie cambie el estado guardado.
    CHECKOUT_PENDING = 'checkout_pending'
    STAGES = {
        Status.PENDING: ('Inscripción pendiente', 'ESTAFA está revisando tu inscripción. Podés seguir editándola mientras tanto.'),
        Status.ACTIVE: ('Inscripción aprobada', 'Tu instalación está confirmada. Completá cada sección antes de su cierre.'),
        CHECKOUT_PENDING: ('Checkout pendiente', 'Cuando retires la instalación y limpies el espacio, completá el checkout y envialo.'),
        Status.CHECKOUT_SUBMITTED: ('Checkout enviado', 'El equipo de Arte va a verificar el retiro y la limpieza.'),
        Status.CHECKOUT_VERIFIED: ('Checkout verificado', '¡Gracias por tu instalación!'),
        Status.REJECTED: ('Rechazada', 'ESTAFA no aceptó esta instalación para esta edición.'),
    }

    class BenefitStatus(models.TextChoices):
        NOT_EVALUATED = 'none', 'Sin evaluar'
        ELIGIBLE = 'eligible', 'Elegible'
        GRANTED = 'granted', 'Otorgado'
        NOT_ELIGIBLE = 'ineligible', 'No elegible'

    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name='artworks')
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='owned_artworks', verbose_name='Responsable')
    # Personas del equipo que pueden editar la instalación, además de su responsable.
    collaborators = models.ManyToManyField(User, blank=True, related_name='collaborative_artworks', verbose_name='Pueden editar')
    operations_group = models.OneToOneField(
        'events.Grupo', on_delete=models.SET_NULL, null=True, blank=True, related_name='artwork', verbose_name='Equipo',
    )
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.PLANNED, verbose_name='Modalidad')

    title = models.CharField(max_length=120, blank=True, verbose_name='Nombre de la instalación')
    proposal = models.TextField(blank=True, verbose_name='Descripción de la propuesta')
    dimensions = models.CharField(max_length=200, blank=True, verbose_name='Dimensiones')
    materials = models.TextField(blank=True, verbose_name='Materiales')
    technical_needs = models.TextField(blank=True, verbose_name='Necesidades técnicas y energía')
    uses_sound = models.BooleanField(default=False, verbose_name='La instalación utiliza sonido amplificado')
    safety_plan = models.TextField(blank=True, verbose_name='Seguridad y uso de fuego')
    uses_fire = models.BooleanField(default=False, verbose_name='La instalación utiliza fuego')
    fire_details = models.TextField(blank=True, verbose_name='Combustible, cantidad y funcionamiento del fuego')
    extinguishing_plan = models.TextField(blank=True, verbose_name='Plan y elementos de extinción')

    class BurnCompany(models.TextChoices):
        ALONE = 'alone', 'Sola'
        SHARED = 'shared', 'Junto con otras instalaciones'
        EITHER = 'either', 'Me da igual'

    burns = models.BooleanField(default=False, verbose_name='La instalación se quema')
    burn_preferred_time = models.CharField(
        max_length=200, blank=True, verbose_name='¿Cuándo preferís quemarla?',
        help_text='Por ejemplo: sábado a la noche, después de la quema principal.',
    )
    burn_company = models.CharField(
        max_length=10, blank=True, choices=BurnCompany.choices,
        verbose_name='¿La querés quemar sola o junto con otras instalaciones?',
    )
    files_url = models.URLField(
        max_length=500, blank=True, verbose_name='Link a más archivos',
        help_text='Una carpeta con más material, por ejemplo de Google Drive o Dropbox.',
    )
    power_watts = models.PositiveIntegerField(null=True, blank=True, verbose_name='Potencia eléctrica máxima (W)')
    safety_contact = models.CharField(max_length=200, blank=True, verbose_name='Responsable de seguridad durante el evento')
    safety_responsible = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='safety_responsible_artworks', verbose_name='Responsable de seguridad',
    )

    grant_requested = models.BooleanField(default=False, verbose_name='Quiero solicitar una beca')
    grant_justification = models.TextField(blank=True, verbose_name='Por qué la beca hace posible la instalación')
    grant_status = models.CharField(max_length=10, choices=GrantStatus.choices, default=GrantStatus.NOT_REQUESTED, verbose_name='Estado de la beca')
    grant_report = models.TextField(blank=True, verbose_name='Rendición y resultado de la instalación')
    grant_approved_amount_ars = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(1)], verbose_name='Monto aprobado en ARS')
    grant_decision_notes = models.TextField(blank=True, verbose_name='Devolución sobre la beca')
    grant_paid_at = models.DateField(null=True, blank=True, verbose_name='Fecha de pago de la beca')
    grant_payment_reference = models.CharField(max_length=200, blank=True, verbose_name='Referencia del pago')

    public_title = models.CharField(max_length=80, blank=True, verbose_name='Título para el desplegable')
    public_description = models.CharField(max_length=500, blank=True, verbose_name='Descripción para el desplegable')
    preferred_location = models.CharField(max_length=200, blank=True, verbose_name='Ubicación preferida')
    assigned_location = models.CharField(max_length=200, blank=True, verbose_name='Ubicación asignada')
    placement_notes = models.TextField(blank=True, verbose_name='Notas de placement')

    arrival_date = models.DateField(null=True, blank=True, verbose_name='Fecha general de ingreso anticipado')
    departure_date = models.DateField(null=True, blank=True, verbose_name='Fecha general de desarme y salida')
    crew = models.TextField(blank=True, verbose_name='Equipo que ingresa')
    providers = models.TextField(blank=True, verbose_name='Proveedores y vehículos')

    checkin_arrived_at = models.DateTimeField(null=True, blank=True, verbose_name='Hora de llegada de la instalación al evento')
    checkin_art_at = models.DateTimeField(null=True, blank=True, verbose_name='Check-in realizado con Arte')
    checkin_art_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='artwork_checkins', verbose_name='Check-in registrado por',
    )
    checkin_placed = models.BooleanField(default=False, verbose_name='La instalación quedó ubicada')
    checkin_placement_changed = models.BooleanField(default=False, verbose_name='El placement original cambió')
    checkin_placement_change_notes = models.TextField(blank=True, verbose_name='Cambio de placement y motivo')

    checkout_completed = models.BooleanField(default=False, verbose_name='Solicito verificar el retiro y limpieza')
    checkout_team_responsible = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='team_checkout_artworks', verbose_name='Responsable del equipo de la instalación',
    )
    checkout_notes = models.TextField(blank=True, verbose_name='Notas de checkout')
    checkout_requested_at = models.DateTimeField(null=True, blank=True, verbose_name='Checkout solicitado')
    checkout_verified_at = models.DateTimeField(null=True, blank=True, verbose_name='Checkout verificado')
    checkout_verified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='verified_artwork_checkouts')
    checkout_staff_notes = models.TextField(
        blank=True, verbose_name='Comentarios de ESTAFA',
        help_text='Sólo los ve ESTAFA: el equipo de la instalación no los ve.',
    )
    understanding_letter = models.FileField(
        upload_to='art/understanding_letters', storage=private_art_storage,
        blank=True, verbose_name='Declaración de entendimiento digital',
    )
    understanding_letter_physical_received = models.BooleanField(
        default=False, verbose_name='Copia física recibida',
    )
    understanding_letter_physical_received_at = models.DateField(
        null=True, blank=True, verbose_name='Fecha de recepción de la copia física',
    )
    understanding_letter_physical_custodian = models.CharField(
        max_length=200, blank=True, verbose_name='Quién tiene la copia física',
    )
    understanding_letter_physical_notes = models.TextField(
        blank=True, verbose_name='Ubicación o comentarios sobre la copia física',
    )
    understanding_letter_physical_waiver = models.BooleanField(
        default=False, verbose_name='Excepción de entrega previa por distancia a CABA',
        help_text='Autoriza no entregarla previamente en CABA; igualmente debe entregarse en el evento antes de empezar a construir.',
    )
    understanding_letter_physical_waiver_reason = models.TextField(
        blank=True, verbose_name='Motivo de la excepción de la copia física',
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, verbose_name='Estado de la instalación')
    status_changed_at = models.DateTimeField(null=True, blank=True, verbose_name='Cambio de estado')
    status_changed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='artwork_status_changes', verbose_name='Estado cambiado por',
    )
    estafa_contact = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='estafa_contact_artworks', verbose_name='Contacto de ESTAFA',
    )
    review_feedback = models.TextField(blank=True, verbose_name='Mensaje de ESTAFA al equipo de la instalación')
    benefit_status = models.CharField(max_length=10, choices=BenefitStatus.choices, default=BenefitStatus.NOT_EVALUATED, verbose_name='Beneficio para la próxima edición')
    benefit_notes = models.TextField(blank=True, verbose_name='Notas del beneficio')
    version = models.PositiveIntegerField(default=1, editable=False)
    submitted_at = models.DateTimeField(null=True, blank=True, verbose_name='Enviada')

    class Meta:
        ordering = ['-updated_at']
        verbose_name = 'Instalación de Arte'
        verbose_name_plural = 'Instalaciones de Arte'

    def __str__(self):
        return f'{self.title or "Instalación sin título"} · {self.event.name}'

    def can_edit(self, user):
        return user == self.owner or self.collaborators.filter(pk=user.pk).exists()

    def team_members(self):
        from events.models import GrupoMiembro

        if not self.operations_group_id:
            return GrupoMiembro.objects.none()
        return self.operations_group.miembros.select_related('user__profile')

    def is_team_member(self, user):
        return self.team_members().filter(user=user).exists()

    def can_manage_team(self, user):
        """Sumar y quitar personas: quienes editan la instalación y ESTAFA."""
        return self.can_edit(user) or self.can_manage(user)

    def can_grant_edit(self, user):
        """Dar o quitar permiso de edición: solo la persona responsable y ESTAFA."""
        return user == self.owner or self.can_manage(user)

    def can_manage(self, user):
        return can_coordinate(user, self.event)

    @property
    def stage(self):
        if self.status == self.Status.ACTIVE:
            try:
                if self.event.art_program.checkout_is_open():
                    return self.CHECKOUT_PENDING
            except ArtProgram.DoesNotExist:
                pass
        return self.status

    @property
    def stage_label(self):
        return self.STAGES[self.stage][0]

    @property
    def stage_hint(self):
        return self.STAGES[self.stage][1]

    def set_status(self, status, user):
        self.status = status
        self.status_changed_at = timezone.now()
        self.status_changed_by = user

    def clean(self):
        errors = {}
        if self.event_id and self.public_description:
            try:
                description_limit = self.event.art_program.public_description_max_length
            except ArtProgram.DoesNotExist:
                description_limit = None
            description_changed = (
                not self.pk
                or Artwork.objects.filter(pk=self.pk).exclude(public_description=self.public_description).exists()
            )
            if description_limit and description_changed and len(self.public_description) > description_limit:
                errors['public_description'] = f'La descripción puede tener hasta {description_limit} caracteres.'
        if self.checkout_verified_at and not self.checkout_completed:
            errors['checkout_verified_at'] = 'El equipo de la instalación debe solicitar el checkout antes de verificarlo.'
        if self.understanding_letter_physical_waiver and not self.understanding_letter_physical_waiver_reason:
            errors['understanding_letter_physical_waiver_reason'] = 'Indicá por qué corresponde la excepción por distancia a CABA.'
        if self.checkin_art_at and not self.checkin_arrived_at:
            errors['checkin_arrived_at'] = 'Indicá primero la hora de llegada de la instalación.'
        if self.checkin_placement_changed and not self.checkin_placed:
            errors['checkin_placed'] = 'Marcá que la instalación quedó ubicada antes de registrar un cambio de placement.'
        if self.checkin_placement_changed and not self.checkin_placement_change_notes:
            errors['checkin_placement_change_notes'] = 'Explicá el cambio respecto del placement original.'
        if self.understanding_letter_physical_received and not (
            (self.understanding_letter_physical_custodian or '').strip()
            or (self.understanding_letter_physical_notes or '').strip()
        ):
            errors['understanding_letter_physical_notes'] = 'Indicá quién tiene la copia física o dónde está guardada.'
        if errors:
            raise ValidationError(errors)

    def grant_total_ars(self, phase):
        return sum((item.amount_ars for item in self.grant_items.filter(phase=phase)), Decimal('0.00'))

    @property
    def budget_total_ars(self):
        return self.grant_total_ars(ArtworkGrantItem.Phase.BUDGET)

    @property
    def expense_total_ars(self):
        return self.grant_total_ars(ArtworkGrantItem.Phase.EXPENSE)


class ArtworkGrantItem(BaseModel):
    class Phase(models.TextChoices):
        BUDGET = 'budget', 'Presupuesto'
        EXPENSE = 'expense', 'Rendición'

    class Currency(models.TextChoices):
        ARS = 'ARS', 'Pesos argentinos (ARS)'
        USD = 'USD', 'Dólares estadounidenses (USD)'

    class ItemType(models.TextChoices):
        MATERIALS = 'materials', 'Materiales'
        LABOR = 'labor', 'Mano de obra'
        SERVICE = 'service', 'Servicio'
        OTHER = 'other', 'Otro'

    class ReviewStatus(models.TextChoices):
        PENDING = 'pending', 'Pendiente de revisión'
        APPROVED = 'approved', 'Aceptado'
        REJECTED = 'rejected', 'Rechazado'

    artwork = models.ForeignKey(Artwork, on_delete=models.CASCADE, related_name='grant_items')
    phase = models.CharField(max_length=8, choices=Phase.choices)
    item_type = models.CharField(max_length=10, choices=ItemType.choices, default=ItemType.OTHER, verbose_name='Tipo')
    concept = models.CharField(max_length=200, verbose_name='Concepto')
    details = models.TextField(blank=True, verbose_name='Detalle')
    amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))], verbose_name='Monto original')
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.ARS, verbose_name='Moneda')
    exchange_rate = models.DecimalField(max_digits=14, decimal_places=4, default=1, validators=[MinValueValidator(Decimal('0.0001'))], verbose_name='Cotización ARS por USD')
    rate_date = models.DateField(verbose_name='Fecha de cotización o pago')
    rate_source = models.CharField(max_length=200, blank=True, verbose_name='Fuente y tipo de cambio')
    review_status = models.CharField(
        max_length=10, choices=ReviewStatus.choices, default=ReviewStatus.PENDING,
        verbose_name='Revisión del ítem',
    )
    review_notes = models.TextField(blank=True, verbose_name='Comentarios de la revisión')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ['phase', 'created_at']
        verbose_name = 'Ítem de beca'
        verbose_name_plural = 'Ítems de beca'
        constraints = [
            models.CheckConstraint(check=Q(phase__in=('budget', 'expense')), name='art_grant_item_valid_phase'),
            models.CheckConstraint(check=Q(currency__in=('ARS', 'USD')), name='art_grant_item_valid_currency'),
            models.CheckConstraint(check=Q(amount__gt=0), name='art_grant_item_positive_amount'),
            models.CheckConstraint(check=Q(exchange_rate__gt=0), name='art_grant_item_positive_rate'),
            models.CheckConstraint(check=Q(currency='USD') | Q(exchange_rate=1), name='art_grant_item_ars_rate_one'),
        ]

    def clean(self):
        if self.currency == self.Currency.ARS:
            self.exchange_rate = Decimal('1')
            self.rate_source = ''
        elif not self.rate_source:
            raise ValidationError({'rate_source': 'Indicá la fuente y el tipo de dólar utilizado.'})

    @property
    def amount_ars(self):
        return (self.amount * self.exchange_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def __str__(self):
        return f'{self.get_phase_display()} · {self.concept}'


class ArtworkGrantItemPhoto(BaseModel):
    item = models.ForeignKey(ArtworkGrantItem, on_delete=models.CASCADE, related_name='photos')
    image = models.ImageField(upload_to='art/grants', storage=private_art_storage)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Imagen de ítem de beca'
        verbose_name_plural = 'Imágenes de ítems de beca'


ART_DOCUMENT_TYPE_CHOICES = (
    ('DNI', 'DNI'),
    ('PASSPORT', 'Pasaporte'),
    ('ID_CARD', 'Cédula de identidad'),
    ('OTHER', 'Otro'),
)


class ArtworkProvider(BaseModel):
    artwork = models.ForeignKey(Artwork, on_delete=models.CASCADE, related_name='artwork_providers')
    company_name = models.CharField(max_length=200, verbose_name='Proveedor o empresa')
    contact_first_name = models.CharField(max_length=100, verbose_name='Nombre del contacto')
    contact_last_name = models.CharField(max_length=100, verbose_name='Apellido del contacto')
    email = models.EmailField()
    phone = models.CharField(max_length=30, verbose_name='Teléfono')
    service_description = models.TextField(verbose_name='Servicio o materiales que entrega')
    for_entry = models.BooleanField(default=True, verbose_name='Se usa para el ingreso anticipado')
    for_exit = models.BooleanField(default=True, verbose_name='Se usa para el desarme y salida')
    early_entry_at = models.DateTimeField(null=True, blank=True, verbose_name='Entrada al predio')
    early_exit_at = models.DateTimeField(null=True, blank=True, verbose_name='Salida del predio')
    dismantling_entry_at = models.DateTimeField(null=True, blank=True, verbose_name='Entrada al predio para desarme')
    dismantling_exit_at = models.DateTimeField(null=True, blank=True, verbose_name='Salida final del predio')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ['company_name']
        verbose_name = 'Proveedor de instalación'
        verbose_name_plural = 'Proveedores de instalación'
        constraints = [
            models.CheckConstraint(check=Q(for_entry=True) | Q(for_exit=True), name='art_provider_has_operation'),
        ]

    def __str__(self):
        return self.company_name


class ArtworkProviderVehicle(BaseModel):
    class VehicleType(models.TextChoices):
        CAR = 'car', 'Auto'
        VAN = 'van', 'Utilitario'
        TRUCK = 'truck', 'Camión'
        TRAILER = 'trailer', 'Acoplado'
        OTHER = 'other', 'Otro'

    provider = models.ForeignKey(ArtworkProvider, on_delete=models.CASCADE, related_name='vehicles')
    vehicle_type = models.CharField(max_length=10, choices=VehicleType.choices, default=VehicleType.CAR, verbose_name='Tipo de vehículo')
    plate = models.CharField(max_length=20, verbose_name='Patente')
    make_model = models.CharField(max_length=150, verbose_name='Marca y modelo')
    driver_name = models.CharField(max_length=200, verbose_name='Nombre y apellido del conductor')
    driver_document_type = models.CharField(max_length=10, choices=ART_DOCUMENT_TYPE_CHOICES, default='DNI', verbose_name='Tipo de documento del conductor')
    driver_document_number = models.CharField(max_length=40, verbose_name='Documento del conductor')
    notes = models.TextField(blank=True, verbose_name='Notas del vehículo')

    class Meta:
        ordering = ['plate']
        verbose_name = 'Vehículo de proveedor de instalación'
        verbose_name_plural = 'Vehículos de proveedores de instalación'
        constraints = [
            models.UniqueConstraint(fields=['provider', 'plate'], name='unique_artwork_provider_plate'),
        ]

    def __str__(self):
        return f'{self.plate} · {self.provider}'


class ArtworkPhoto(BaseModel):
    class Stage(models.TextChoices):
        PROPOSAL = 'proposal', 'Propuesta'
        PROCESS = 'process', 'Proceso'
        FINAL = 'final', 'Instalación terminada'
        GRANT_REPORT = 'grant', 'Rendición de beca'

    artwork = models.ForeignKey(Artwork, on_delete=models.CASCADE, related_name='photos')
    image = models.ImageField(upload_to='art/gallery', storage=private_art_storage)
    stage = models.CharField(max_length=10, choices=Stage.choices, default=Stage.PROCESS, verbose_name='Etapa')
    caption = models.TextField(blank=True, verbose_name='Descripción o crédito')
    publication_authorized = models.BooleanField(default=False, verbose_name='Autorizada para publicación')
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Foto de instalación'
        verbose_name_plural = 'Fotos de instalaciones'

    def __str__(self):
        return f'{self.get_stage_display()} · {self.artwork}'


class ArtworkFile(BaseModel):
    """Archivos de la propuesta: bocetos, planos o renders, en imagen o PDF."""

    IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif')

    artwork = models.ForeignKey(Artwork, on_delete=models.CASCADE, related_name='files')
    file = models.FileField(upload_to='art/files', storage=private_art_storage, verbose_name='Archivo')
    name = models.CharField(max_length=255, verbose_name='Nombre')
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Archivo de instalación'
        verbose_name_plural = 'Archivos de instalaciones'

    def __str__(self):
        return f'{self.name} · {self.artwork}'

    @property
    def is_image(self):
        return self.file.name.lower().endswith(self.IMAGE_EXTENSIONS)


class ArtworkCheckoutPhoto(BaseModel):
    class Category(models.TextChoices):
        DIRT = 'dirt', 'M.U.G.R.E.'
        ENVIRONMENTAL_DAMAGE = 'environmental_damage', 'Daño ambiental'
        ARTWORK_PARTS = 'artwork_parts', 'Partes de la instalación'
        BURN_REMAINS = 'burn_remains', 'Restos de quema'
        CLEANUP = 'cleanup', 'Limpieza y estado final'
        OTHER = 'other', 'Otro'

    artwork = models.ForeignKey(Artwork, on_delete=models.CASCADE, related_name='checkout_photos')
    image = models.ImageField(upload_to='art/checkout', storage=private_art_storage)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER, verbose_name='Categoría')
    caption = models.TextField(blank=True, verbose_name='Detalle')
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Foto de checkout de instalación'
        verbose_name_plural = 'Fotos de checkout de instalaciones'

    def __str__(self):
        return f'{self.get_category_display()} · {self.artwork}'


auditlog.register(ArtProgram)
auditlog.register(Artwork)
auditlog.register(ArtworkGrantItem)
auditlog.register(ArtworkGrantItemPhoto)
auditlog.register(ArtworkProvider)
auditlog.register(ArtworkProviderVehicle)
auditlog.register(ArtworkPhoto)
auditlog.register(ArtworkFile)
auditlog.register(ArtworkCheckoutPhoto)
