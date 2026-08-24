import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import models
from django.db.models import Count, Sum, Q
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.utils import timezone
from django.utils.text import slugify
from django.contrib.auth.models import User

from auditlog.registry import auditlog

from utils.models import BaseModel


def default_art_reminder_days():
    return [7, 3, 1]


def private_art_storage():
    if getattr(settings, 'DEFAULT_FILE_STORAGE', '') == 'django_s3_storage.storage.S3Storage':
        from django_s3_storage.storage import S3Storage
        return S3Storage(aws_s3_bucket_auth=True, aws_s3_key_prefix='private')
    return default_storage


class Event(BaseModel):
    active = models.BooleanField(default=True, help_text="Event is active and can be accessed")
    is_main = models.BooleanField(default=False, help_text="Main event displayed at /")
    slug = models.SlugField(max_length=100, unique=True, null=True, blank=True, help_text="URL-friendly identifier for the event")
    name = models.CharField(max_length=255)
    location = models.CharField(max_length=255, blank=True, help_text="Location of the event")
    location_url = models.URLField(max_length=500, blank=True, help_text="URL for the event location (e.g. Google Maps link)")
    has_volunteers = models.BooleanField(default=False)
    start = models.DateTimeField()
    end = models.DateTimeField()
    max_tickets = models.IntegerField(blank=True, null=True)
    max_tickets_per_order = models.IntegerField(default=5)
    transfers_enabled_until = models.DateTimeField()
    volunteers_enabled_until = models.DateTimeField(blank=True, null=True)
    send_transfer_notifications = models.BooleanField(default=False, help_text="If checked, transfer notification emails will be sent for this event")
    ingreso_anticipado_limite_carga = models.DateTimeField(blank=True, null=True, help_text="Fecha límite hasta la cual se pueden cargar o modificar ingresos anticipados. Si es null, no hay límite.")
    show_multiple_tickets = models.BooleanField(default=False,
                                                help_text="If unchecked, only the chepeast ticket will be shown.")
    show_door_remaining = models.BooleanField(
        default=False,
        help_text="Si está marcado, en checkout sin entradas online se informa cuántas quedan en puerta.",
    )

    # homepage
    header_image = models.ImageField(upload_to='events/heros', help_text=u"Dimensions: 1666px x 500px")
    title = models.TextField()
    description = models.TextField()

    attendee_must_be_registered = models.BooleanField(default=True, help_text="If checked, all attendees must be registered users")
    
    admins = models.ManyToManyField(User, blank=True, related_name='admin_events', help_text="Users who can administer this event")
    access_scanner = models.ManyToManyField(User, blank=True, related_name='scanner_events', help_text="Users who can access the scanner for this event")
    access_caja = models.ManyToManyField(User, blank=True, related_name='caja_events', help_text="Users who can access the caja for this event")
    
    # Venue capacity and occupancy tracking
    venue_capacity = models.PositiveIntegerField(null=True, blank=True, help_text="Maximum venue capacity (optional)")
    attendees_left = models.PositiveIntegerField(default=0, help_text="Number of attendees who have left the venue")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['is_main'], condition=Q(is_main=True), name='unique_main_event')
        ]
        permissions = [
            ("view_tickets_sold_report", "Can view tickets sold report"),
        ]

    def __str__(self):
        return self.name

    def clean(self, *args, **kwargs):
        if not self.slug and self.name:
            self.slug = slugify(self.name)
        return super().clean(*args, **kwargs)

    def save(self, *args, **kwargs):
        if self.is_main:
            qs = Event.objects.filter(is_main=True)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            qs.update(is_main=False)
        super().save(*args, **kwargs)

    def tickets_remaining(self):
        from tickets.models import Order, OrderTicket

        if self.max_tickets:
            # Sum quantities on OrderTicket rows that count toward the cap.
            # Do not aggregate from Order + Sum(order_tickets__quantity): Django sums
            # *all* lines on each order, including ticket types with ignore_max_amount=True,
            # which inflates tickets_sold and blocks sales early.
            tickets_sold = (
                OrderTicket.objects.filter(
                    order__status=Order.OrderStatus.CONFIRMED,
                    ticket_type__event=self,
                    ticket_type__ignore_max_amount=False,
                ).aggregate(total=Sum('quantity'))['total']
                or 0
            )
            return self.max_tickets - tickets_sold
        else:
            return 999999999  # extra high number (easy hack)

    def door_tickets_remaining(self):
        """Stock disponible en puerta (tipos con show_in_caja), acotado al cupo del evento."""
        from tickets.models import TicketType

        caja_stock = (
            TicketType.objects.filter(
                event=self,
                show_in_caja=True,
                is_direct_type=False,
                ticket_count__gt=0,
            ).aggregate(total=Sum('ticket_count'))['total']
            or 0
        )
        if self.max_tickets:
            return min(caja_stock, max(0, self.tickets_remaining() or 0))
        return caja_stock

    def volunteer_period(self):
        if self.end < timezone.now():
            return False
        if self.volunteers_enabled_until and self.volunteers_enabled_until < timezone.now():
            return False
        return True

    def transfer_period(self):
        if self.end < timezone.now():
            return False
        if self.transfers_enabled_until and self.transfers_enabled_until < timezone.now():
            return False
        return True

    @property
    def donations_art(self):
        from tickets.models import Order
        return self.orders.filter(
            status=Order.OrderStatus.CONFIRMED,
            donation_art__isnull=False
        ).aggregate(total=Sum('donation_art'))['total'] or 0

    @property
    def donations_venue(self):
        from tickets.models import Order
        return self.orders.filter(
            status=Order.OrderStatus.CONFIRMED,
            donation_venue__isnull=False
        ).aggregate(total=Sum('donation_venue'))['total'] or 0

    @property
    def donations_grant(self):
        from tickets.models import Order
        return self.orders.filter(
            status=Order.OrderStatus.CONFIRMED,
            donation_grant__isnull=False
        ).aggregate(total=Sum('donation_grant'))['total'] or 0

    @classmethod
    def get_main_event(cls):
        """Get the main event (displayed at /).

        Prefer a current/upcoming main. If every event has ended, keep showing
        the last finished active event instead of returning None.
        """
        from events.services.main_event import reconcile_main_event

        reconcile_main_event()
        now = timezone.now()
        current = cls.objects.filter(
            is_main=True,
            active=True,
            end__gte=now,
        ).first()
        if current:
            return current

        # Reconcile keeps an expired main when there is no replacement.
        expired_main = cls.objects.filter(is_main=True, active=True).first()
        if expired_main:
            return expired_main

        return cls.objects.filter(active=True).order_by('-end', '-id').first()

    @classmethod
    def get_active_events(cls):
        """Get all active events"""
        return cls.objects.filter(active=True)

    @classmethod
    def get_by_slug(cls, slug):
        """Get event by slug"""
        try:
            return cls.objects.get(slug=slug, active=True)
        except cls.DoesNotExist:
            return None

    @property
    def venue_occupancy(self):
        """Calculate current venue occupancy as used tickets minus attendees who left"""
        from tickets.models import NewTicket
        used_tickets = NewTicket.objects.filter(
            event=self, 
            is_used=True
        ).count()
        return max(0, used_tickets - self.attendees_left)

    @property
    def occupancy_percentage(self):
        """Calculate venue occupancy percentage"""
        if self.venue_capacity and self.venue_capacity > 0:
            return (self.venue_occupancy / self.venue_capacity) * 100
        return 0


class EventTermsAndConditions(BaseModel):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='terms_and_conditions')
    title = models.CharField(max_length=255, help_text="Título del término y condición")
    slug = models.SlugField(max_length=100, unique=True, null=True, blank=True, help_text="URL-friendly identifier for the term")
    description = models.TextField(blank=True, null=True, help_text="Descripción detallada (opcional, puede contener HTML)")
    order = models.IntegerField(default=0, help_text="Orden de visualización (menor número aparece primero)")

    class Meta:
        verbose_name = "Términos y Condiciones"
        verbose_name_plural = "Términos y Condiciones"
        ordering = ['order', 'id']

    def __str__(self):
        return f"{self.title} - {self.event.name}"
    
    def save(self, *args, **kwargs):
        # Auto-generate slug from title if not provided
        if not self.slug and self.title:
            base_slug = slugify(self.title)
            # Truncate to ensure it fits in max_length (100)
            # Reserve space for event slug prefix and counter suffix if needed
            max_base_length = 80  # Reserve 20 chars for prefix/suffix
            if len(base_slug) > max_base_length:
                base_slug = base_slug[:max_base_length]
            
            # Ensure uniqueness by appending event slug if needed
            if self.event:
                event_slug = slugify(self.event.name)
                # Truncate event slug if needed
                if len(event_slug) > 20:
                    event_slug = event_slug[:20]
                base_slug = f"{event_slug}-{base_slug}"
                # Truncate again after adding event prefix
                if len(base_slug) > 95:  # Reserve 5 chars for counter
                    base_slug = base_slug[:95]
            
            # Check if slug already exists
            slug = base_slug
            counter = 1
            while EventTermsAndConditions.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                # Truncate base to make room for counter
                counter_str = f"-{counter}"
                max_base_for_counter = 100 - len(counter_str)
                slug_base = base_slug[:max_base_for_counter] if len(base_slug) > max_base_for_counter else base_slug
                slug = f"{slug_base}{counter_str}"
                counter += 1
                # Safety check to prevent infinite loop
                if counter > 9999:
                    # Use a hash-based approach as fallback
                    import hashlib
                    slug_hash = hashlib.md5(f"{self.title}{self.event.id if self.event else ''}".encode()).hexdigest()[:8]
                    slug = f"term-{slug_hash}"
                    break
            self.slug = slug
        return super().save(*args, **kwargs)


class EventTermsAndConditionsAcceptance(BaseModel):
    """Registro de aceptación de términos y condiciones por usuario"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='terms_acceptances')
    term = models.ForeignKey('EventTermsAndConditions', on_delete=models.CASCADE, related_name='acceptances')
    order = models.ForeignKey('tickets.Order', on_delete=models.SET_NULL, null=True, blank=True, 
                             help_text="Orden asociada a esta aceptación")
    accepted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Aceptación de Términos y Condiciones"
        verbose_name_plural = "Aceptaciones de Términos y Condiciones"
        unique_together = [['user', 'term']]
        ordering = ['-accepted_at']

    def __str__(self):
        return f"{self.user.email} - {self.term.title} ({self.term.event.name})"


class GrupoTipo(BaseModel):
    """Tipos de grupos (ARTE, CAMP, CAOS, etc)"""
    nombre = models.CharField(max_length=100, unique=True, help_text="Nombre del tipo de grupo (ej: ARTE, CAMP, CAOS)")
    descripcion = models.TextField(blank=True, null=True, help_text="Descripción opcional del tipo de grupo")
    activo = models.BooleanField(default=True, help_text="Indica si el tipo de grupo está activo")

    class Meta:
        verbose_name = "Tipo de Grupo"
        verbose_name_plural = "Tipos de Grupo"
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class Grupo(BaseModel):
    """Grupos de usuarios asociados a un evento"""
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='grupos', help_text="Evento al que pertenece el grupo")
    lider = models.ForeignKey(User, on_delete=models.CASCADE, related_name='grupos_liderados', verbose_name="Responsable", help_text="Usuario responsable del grupo")
    nombre = models.CharField(max_length=255, help_text="Nombre del grupo")
    tipo = models.ForeignKey(GrupoTipo, on_delete=models.RESTRICT, related_name='grupos', help_text="Tipo de grupo")
    ingreso_anticipado_amount = models.PositiveIntegerField(default=0, help_text="Cantidad máxima de personas que pueden tener ingreso anticipado")
    ingreso_anticipado_desde = models.DateTimeField(null=True, blank=True, help_text="Fecha y hora desde la cual se puede hacer ingreso anticipado")
    late_checkout_hasta = models.DateTimeField(null=True, blank=True, help_text="Fecha y hora hasta la cual se puede hacer late checkout")
    late_checkout_amount = models.PositiveIntegerField(default=0, help_text="Cantidad máxima de personas que pueden tener late checkout")

    class Meta:
        verbose_name = "Grupo"
        verbose_name_plural = "Grupos"
        ordering = ['tipo__nombre', 'nombre']

    def __str__(self):
        return f"{self.tipo.nombre} - {self.nombre} ({self.event.name})"

    def miembros_count(self):
        """Retorna la cantidad de miembros del grupo"""
        return self.miembros.count()

    def ingreso_anticipado_count(self):
        """Retorna la cantidad de miembros con ingreso anticipado"""
        return self.miembros.filter(ingreso_anticipado=True).count()

    def puede_agregar_ingreso_anticipado(self):
        """Verifica si se pueden agregar más personas con ingreso anticipado"""
        return self.ingreso_anticipado_count() < self.ingreso_anticipado_amount

    def late_checkout_count(self):
        """Retorna la cantidad de miembros con late checkout"""
        return self.miembros.filter(late_checkout=True).count()

    def puede_agregar_late_checkout(self):
        """Verifica si se pueden agregar más personas con late checkout"""
        return self.late_checkout_count() < self.late_checkout_amount


class GrupoMiembro(BaseModel):
    """Miembros de un grupo"""
    RESTRICCION_CHOICES = [
        ('sin_restricciones', 'Sin Restricciones'),
        ('vegetarian', 'Vegetarian'),
        ('sin_tacc', 'Sin TACC'),
        ('particular', 'Particular'),
    ]
    
    grupo = models.ForeignKey(Grupo, on_delete=models.CASCADE, related_name='miembros', help_text="Grupo al que pertenece el miembro")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='grupos_miembro', help_text="Usuario miembro del grupo")
    ingreso_anticipado = models.BooleanField(default=False, help_text="Indica si el miembro tiene ingreso anticipado")
    ingreso_anticipado_fecha = models.DateField(null=True, blank=True, help_text="Fecha de ingreso anticipado (entre desde y fecha de inicio del evento)")
    late_checkout = models.BooleanField(default=False, help_text="Indica si el miembro tiene late checkout")
    restriccion = models.CharField(
        max_length=20,
        choices=RESTRICCION_CHOICES,
        default='sin_restricciones',
        help_text="Restricción alimentaria del miembro"
    )

    class Meta:
        verbose_name = "Miembro de Grupo"
        verbose_name_plural = "Miembros de Grupo"
        unique_together = [['grupo', 'user']]
        ordering = ['-ingreso_anticipado', '-late_checkout', 'user__email']

    def clean(self):
        """Valida que el usuario tenga un bono para el evento del grupo"""
        from tickets.models import NewTicket
        
        # No validar si es el responsable (se agrega automáticamente)
        if self.grupo and self.grupo.lider == self.user:
            return
        
        # Validar que el usuario tenga un bono (holder y owner) para el evento del grupo
        if self.grupo and self.user:
            has_ticket = NewTicket.objects.filter(
                holder=self.user,
                owner=self.user,
                event=self.grupo.event
            ).exists()
            
            if not has_ticket:
                raise ValidationError(
                    f'El usuario {self.user.email} no tiene un bono vinculado a su nombre para el evento "{self.grupo.event.name}". '
                    'Solo se pueden agregar usuarios que sean dueños de un bono para este evento.'
                )

    def save(self, *args, **kwargs):
        """Sobrescribir save para llamar a clean()"""
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.email} - {self.grupo.nombre}"


@receiver(post_save, sender=Grupo)
def create_grupo_lider_miembro(sender, instance, created, **kwargs):
    """Agrega automáticamente al responsable como miembro del grupo cuando se crea"""
    if created:
        GrupoMiembro.objects.get_or_create(
            grupo=instance,
            user=instance.lider,
            defaults={'ingreso_anticipado': False}
        )


class EventRequest(BaseModel):
    """Propuesta de evento por un miembro de La Sede, revisada por soporte vía Slack (botones) y Chatwoot."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendiente de revisión'
        APPROVED = 'approved', 'Aprobada'
        REJECTED = 'rejected', 'Rechazada'
        CANCELLED = 'cancelled', 'Cancelada'

    requested_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='event_requests',
    )
    name = models.CharField(max_length=255)
    description = models.TextField()
    start = models.DateTimeField()
    end = models.DateTimeField()
    header_image = models.ImageField(upload_to='events/event_requests')
    location = models.CharField(max_length=255)
    location_url = models.URLField(max_length=500, blank=True)
    max_tickets = models.PositiveIntegerField(
        default=300,
        help_text='Cupo máximo total del evento; se replica como stock de cada tipo de entrada.',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    chatwoot_contact_id = models.PositiveIntegerField(null=True, blank=True)
    chatwoot_conversation_id = models.PositiveIntegerField(null=True, blank=True, unique=True)
    slack_channel = models.CharField(blank=True, default='', max_length=64)
    slack_message_ts = models.CharField(blank=True, default='', max_length=32)
    rejection_reason = models.TextField(blank=True)
    created_event = models.ForeignKey(
        Event,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='source_request',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} ({self.get_status_display()})'

    @property
    def is_pending(self):
        return self.status == self.Status.PENDING


class EventRequestTicketType(BaseModel):
    event_request = models.ForeignKey(
        EventRequest,
        on_delete=models.CASCADE,
        related_name='ticket_types',
    )
    name = models.CharField(max_length=100)
    description = models.TextField(max_length=2000, blank=True)
    price = models.DecimalField(decimal_places=2, max_digits=10)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.name} (${self.price})'


class ArtProgram(BaseModel):
    """Fechas que habilitan y bloquean cada bloque del formulario de Arte."""

    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name='art_program')
    is_current = models.BooleanField(default=False, verbose_name='Convocatoria anual vigente')
    registration_opens = models.DateTimeField(null=True, blank=True, verbose_name='Apertura de inscripción')
    registration_closes = models.DateTimeField(null=True, blank=True, verbose_name='Cierre de inscripción')
    proposal_deadline = models.DateTimeField(null=True, blank=True, verbose_name='Cierre de propuesta')
    grants_enabled = models.BooleanField(default=False, verbose_name='Becas habilitadas')
    grant_deadline = models.DateTimeField(null=True, blank=True, verbose_name='Cierre de becas')
    guide_deadline = models.DateTimeField(null=True, blank=True, verbose_name='Cierre de desplegable')
    logistics_deadline = models.DateTimeField(null=True, blank=True, verbose_name='Cierre de logística')
    checkout_opens = models.DateTimeField(null=True, blank=True, verbose_name='Apertura de checkout')
    checkout_deadline = models.DateTimeField(null=True, blank=True, verbose_name='Cierre de checkout')
    understanding_letter_digital_opens = models.DateTimeField(null=True, blank=True, verbose_name='Apertura de carta digital')
    understanding_letter_digital_deadline = models.DateTimeField(null=True, blank=True, verbose_name='Cierre de carta digital')
    understanding_letter_physical_opens = models.DateTimeField(null=True, blank=True, verbose_name='Apertura de carta física')
    understanding_letter_physical_deadline = models.DateTimeField(null=True, blank=True, verbose_name='Cierre de carta física')
    grant_report_deadline = models.DateTimeField(null=True, blank=True, verbose_name='Cierre de rendición de becas')
    reminder_days = models.JSONField(default=default_art_reminder_days, blank=True, verbose_name='Días de anticipación para recordatorios')
    reminder_email_enabled = models.BooleanField(default=True, verbose_name='Recordatorios por email')
    reminder_whatsapp_enabled = models.BooleanField(default=False, verbose_name='Recordatorios por WhatsApp')
    early_entry_slots = models.PositiveIntegerField(default=0, verbose_name='Cupos de ingreso anticipado por obra')
    early_entry_from = models.DateTimeField(null=True, blank=True, verbose_name='Ingreso anticipado desde')
    late_checkout_slots = models.PositiveIntegerField(default=0, verbose_name='Cupos de late checkout por obra')
    late_checkout_until = models.DateTimeField(null=True, blank=True, verbose_name='Late checkout hasta')

    class Meta:
        verbose_name = 'Programa de Arte'
        verbose_name_plural = 'Programas de Arte'
        constraints = [
            models.UniqueConstraint(fields=['is_current'], condition=Q(is_current=True), name='unique_current_art_program'),
        ]

    def __str__(self):
        return f'Arte · {self.event.name}'

    def clean(self):
        errors = {}
        if self.registration_opens and self.registration_closes and self.registration_closes < self.registration_opens:
            errors['registration_closes'] = 'El cierre no puede ser anterior a la apertura.'
        if self.checkout_opens and self.checkout_deadline and self.checkout_deadline < self.checkout_opens:
            errors['checkout_deadline'] = 'El cierre no puede ser anterior a la apertura.'
        for label, opens, deadline in (
            ('digital', self.understanding_letter_digital_opens, self.understanding_letter_digital_deadline),
            ('física', self.understanding_letter_physical_opens, self.understanding_letter_physical_deadline),
        ):
            if opens and deadline and deadline < opens:
                errors[f'understanding_letter_{"digital" if label == "digital" else "physical"}_deadline'] = (
                    f'El cierre de la carta {label} no puede ser anterior a la apertura.'
                )
        if not isinstance(self.reminder_days, list) or any(not isinstance(day, int) or day < 0 for day in self.reminder_days):
            errors['reminder_days'] = 'Usá una lista de días enteros no negativos, por ejemplo [7, 3, 1].'
        if errors:
            raise ValidationError(errors)

    def registration_is_open(self, at=None):
        at = at or timezone.now()
        return (
            (not self.registration_opens or self.registration_opens <= at)
            and (not self.registration_closes or at <= self.registration_closes)
        )

    def checkpoint_state(self, block, at=None):
        at = at or timezone.now()
        opens = getattr(self, f'{block}_opens', None)
        if opens and at < opens:
            return 'upcoming'
        deadline = getattr(self, f'{block}_deadline')
        return 'closed' if deadline and at > deadline else 'open'


class Artwork(BaseModel):
    class Kind(models.TextChoices):
        PLANNED = 'planned', 'Obra inscripta'
        POPUP = 'popup', 'Obra espontánea (popup)'

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
        DRAFT = 'draft', 'Borrador'
        SUBMITTED = 'submitted', 'En revisión'
        CHANGES_REQUESTED = 'changes', 'Requiere cambios'
        ACCEPTED = 'accepted', 'Aceptada'
        REJECTED = 'rejected', 'No aceptada'
        INSTALLED = 'installed', 'Instalada'
        COMPLETED = 'completed', 'Finalizada'
        CANCELLED = 'cancelled', 'Cancelada'

    class BenefitStatus(models.TextChoices):
        NOT_EVALUATED = 'none', 'Sin evaluar'
        ELIGIBLE = 'eligible', 'Elegible'
        GRANTED = 'granted', 'Otorgado'
        NOT_ELIGIBLE = 'ineligible', 'No elegible'

    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name='artworks')
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='owned_artworks', verbose_name='Responsable')
    collaborators = models.ManyToManyField(User, blank=True, related_name='collaborative_artworks')
    operations_group = models.OneToOneField('Grupo', on_delete=models.SET_NULL, null=True, blank=True, related_name='artwork')
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.PLANNED, verbose_name='Modalidad')

    title = models.CharField(max_length=120, blank=True, verbose_name='Nombre de la obra')
    proposal = models.TextField(blank=True, verbose_name='Descripción de la propuesta')
    dimensions = models.CharField(max_length=200, blank=True, verbose_name='Dimensiones')
    materials = models.TextField(blank=True, verbose_name='Materiales')
    technical_needs = models.TextField(blank=True, verbose_name='Necesidades técnicas y energía')
    safety_plan = models.TextField(blank=True, verbose_name='Seguridad y uso de fuego')
    uses_fire = models.BooleanField(default=False, verbose_name='La obra utiliza fuego')
    fire_details = models.TextField(blank=True, verbose_name='Combustible, cantidad y funcionamiento del fuego')
    extinguishing_plan = models.TextField(blank=True, verbose_name='Plan y elementos de extinción')
    power_watts = models.PositiveIntegerField(null=True, blank=True, verbose_name='Potencia eléctrica máxima (W)')
    safety_contact = models.CharField(max_length=200, blank=True, verbose_name='Responsable de seguridad durante el evento')
    safety_responsible = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='safety_responsible_artworks', verbose_name='Responsable de seguridad',
    )

    grant_requested = models.BooleanField(default=False, verbose_name='Quiero solicitar una beca')
    grant_justification = models.TextField(blank=True, verbose_name='Por qué la beca hace posible la obra')
    grant_status = models.CharField(max_length=10, choices=GrantStatus.choices, default=GrantStatus.NOT_REQUESTED, verbose_name='Estado de la beca')
    grant_report = models.TextField(blank=True, verbose_name='Rendición y resultado de la obra')
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

    checkin_arrived_at = models.DateTimeField(null=True, blank=True, verbose_name='Hora de llegada de la obra al evento')
    checkin_art_at = models.DateTimeField(null=True, blank=True, verbose_name='Check-in realizado con Arte')
    checkin_art_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='artwork_checkins', verbose_name='Check-in registrado por',
    )
    checkin_placed = models.BooleanField(default=False, verbose_name='La obra quedó ubicada')
    checkin_placement_changed = models.BooleanField(default=False, verbose_name='El placement original cambió')
    checkin_placement_change_notes = models.TextField(blank=True, verbose_name='Cambio de placement y motivo')

    checkout_completed = models.BooleanField(default=False, verbose_name='Solicito verificar el retiro y limpieza')
    checkout_team_responsible = models.ForeignKey(
        'ArtworkLogisticsPerson', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='team_checkout_artworks', verbose_name='Responsable del equipo de la obra',
    )
    checkout_art_responsible = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='art_checkout_assignments', verbose_name='Responsable de Arte',
    )
    checkout_notes = models.TextField(blank=True, verbose_name='Notas de checkout')
    checkout_requested_at = models.DateTimeField(null=True, blank=True, verbose_name='Checkout solicitado')
    checkout_verified_at = models.DateTimeField(null=True, blank=True, verbose_name='Checkout verificado')
    checkout_verified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='verified_artwork_checkouts')
    understanding_letter = models.FileField(
        upload_to='art/understanding_letters', storage=private_art_storage,
        blank=True, verbose_name='Carta de entendimiento digital',
    )
    understanding_letter_physical_received = models.BooleanField(
        default=False, verbose_name='Carta física recibida',
    )
    understanding_letter_physical_custodian = models.CharField(
        max_length=200, blank=True, verbose_name='Responsable de la carta física',
    )
    understanding_letter_physical_notes = models.TextField(
        blank=True, verbose_name='Ubicación o comentarios sobre la carta física',
    )
    understanding_letter_physical_waiver = models.BooleanField(
        default=False, verbose_name='Excepción de entrega previa por distancia a CABA',
        help_text='Autoriza no entregarla previamente en CABA; igualmente debe entregarse en el evento antes de empezar a construir.',
    )
    understanding_letter_physical_waiver_reason = models.TextField(
        blank=True, verbose_name='Motivo de la excepción de carta física',
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT, verbose_name='Estado de la obra')
    review_feedback = models.TextField(blank=True, verbose_name='Devolución al equipo de la obra')
    benefit_status = models.CharField(max_length=10, choices=BenefitStatus.choices, default=BenefitStatus.NOT_EVALUATED, verbose_name='Beneficio para la próxima edición')
    benefit_notes = models.TextField(blank=True, verbose_name='Notas del beneficio')
    version = models.PositiveIntegerField(default=1, editable=False)
    submitted_at = models.DateTimeField(null=True, blank=True, verbose_name='Enviada')

    class Meta:
        ordering = ['-updated_at']
        verbose_name = 'Obra de Arte'
        verbose_name_plural = 'Obras de Arte'

    def __str__(self):
        return f'{self.title or "Obra sin título"} · {self.event.name}'

    def can_edit(self, user):
        return user == self.owner or self.collaborators.filter(pk=user.pk).exists()

    def can_manage(self, user):
        return user.is_superuser or self.event.admins.filter(pk=user.pk).exists()

    def can_administer(self, user):
        return self.can_manage(user) or self.checkout_art_responsible_id == user.pk

    def clean(self):
        errors = {}
        if self.checkout_verified_at and not self.checkout_completed:
            errors['checkout_verified_at'] = 'El equipo de la obra debe solicitar el checkout antes de verificarlo.'
        if self.understanding_letter_physical_waiver and not self.understanding_letter_physical_waiver_reason:
            errors['understanding_letter_physical_waiver_reason'] = 'Indicá por qué corresponde la excepción por distancia a CABA.'
        if self.checkin_art_at and not self.checkin_arrived_at:
            errors['checkin_arrived_at'] = 'Indicá primero la hora de llegada de la obra.'
        if self.checkin_placement_changed and not self.checkin_placed:
            errors['checkin_placed'] = 'Marcá que la obra quedó ubicada antes de registrar un cambio de placement.'
        if self.checkin_placement_changed and not self.checkin_placement_change_notes:
            errors['checkin_placement_change_notes'] = 'Explicá el cambio respecto del placement original.'
        if self.understanding_letter_physical_received and not (
            (self.understanding_letter_physical_custodian or '').strip()
            or (self.understanding_letter_physical_notes or '').strip()
        ):
            errors['understanding_letter_physical_notes'] = 'Indicá quién tiene la carta física o dónde está guardada.'
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


class ArtworkLogisticsPerson(BaseModel):
    artwork = models.ForeignKey(Artwork, on_delete=models.CASCADE, related_name='logistics_people')
    first_name = models.CharField(max_length=100, verbose_name='Nombre')
    last_name = models.CharField(max_length=100, verbose_name='Apellido')
    email = models.EmailField()
    phone = models.CharField(max_length=30, verbose_name='Teléfono')
    document_type = models.CharField(max_length=10, choices=ART_DOCUMENT_TYPE_CHOICES, default='DNI', verbose_name='Tipo de documento')
    document_number = models.CharField(max_length=40, verbose_name='Número de documento')
    early_entry = models.BooleanField(default=False, verbose_name='Participa del ingreso anticipado')
    early_entry_date = models.DateField(null=True, blank=True, verbose_name='Fecha de ingreso anticipado')
    dismantling = models.BooleanField(default=False, verbose_name='Participa del desarme')
    dismantling_date = models.DateField(null=True, blank=True, verbose_name='Fecha de desarme y salida')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ['last_name', 'first_name']
        verbose_name = 'Persona de logística de obra'
        verbose_name_plural = 'Personas de logística de obra'
        constraints = [
            models.UniqueConstraint(fields=['artwork', 'document_type', 'document_number'], name='unique_artwork_logistics_document'),
        ]

    def __str__(self):
        return f'{self.first_name} {self.last_name}'


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
        verbose_name = 'Proveedor de obra'
        verbose_name_plural = 'Proveedores de obra'
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
        verbose_name = 'Vehículo de proveedor de obra'
        verbose_name_plural = 'Vehículos de proveedores de obra'
        constraints = [
            models.UniqueConstraint(fields=['provider', 'plate'], name='unique_artwork_provider_plate'),
        ]

    def __str__(self):
        return f'{self.plate} · {self.provider}'


class ArtworkPhoto(BaseModel):
    class Stage(models.TextChoices):
        PROPOSAL = 'proposal', 'Propuesta'
        PROCESS = 'process', 'Proceso'
        FINAL = 'final', 'Obra terminada'
        GRANT_REPORT = 'grant', 'Rendición de beca'

    artwork = models.ForeignKey(Artwork, on_delete=models.CASCADE, related_name='photos')
    image = models.ImageField(upload_to='art/gallery', storage=private_art_storage)
    stage = models.CharField(max_length=10, choices=Stage.choices, default=Stage.PROCESS, verbose_name='Etapa')
    caption = models.TextField(blank=True, verbose_name='Descripción o crédito')
    publication_authorized = models.BooleanField(default=False, verbose_name='Autorizada para publicación')
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'Foto de obra'
        verbose_name_plural = 'Fotos de obras'

    def __str__(self):
        return f'{self.get_stage_display()} · {self.artwork}'


class ArtworkCheckoutPhoto(BaseModel):
    class Category(models.TextChoices):
        DIRT = 'dirt', 'M.U.G.R.E.'
        ENVIRONMENTAL_DAMAGE = 'environmental_damage', 'Daño ambiental'
        ARTWORK_PARTS = 'artwork_parts', 'Partes de la obra'
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
        verbose_name = 'Foto de checkout de obra'
        verbose_name_plural = 'Fotos de checkout de obras'

    def __str__(self):
        return f'{self.get_category_display()} · {self.artwork}'


class ArtworkInvitation(BaseModel):
    artwork = models.ForeignKey(Artwork, on_delete=models.CASCADE, related_name='invitations')
    email = models.EmailField()
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    invited_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['email']
        constraints = [
            models.UniqueConstraint(fields=['artwork', 'email'], name='unique_artwork_invitation_email'),
        ]

    @property
    def is_pending(self):
        return not self.accepted_at and not self.revoked_at and self.expires_at >= timezone.now()

    def __str__(self):
        return f'{self.email} · {self.artwork}'


auditlog.register(Event)
auditlog.register(GrupoTipo)
auditlog.register(Grupo)
auditlog.register(GrupoMiembro)
auditlog.register(EventRequest)
auditlog.register(ArtProgram)
auditlog.register(Artwork)
auditlog.register(ArtworkGrantItem)
auditlog.register(ArtworkGrantItemPhoto)
auditlog.register(ArtworkLogisticsPerson)
auditlog.register(ArtworkProvider)
auditlog.register(ArtworkProviderVehicle)
auditlog.register(ArtworkPhoto)
auditlog.register(ArtworkCheckoutPhoto)
auditlog.register(ArtworkInvitation)
