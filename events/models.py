from django.db import models
from django.db.models import Count, Sum, Q
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.text import slugify
from django.contrib.auth.models import User

from auditlog.registry import auditlog

from utils.models import BaseModel


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
    show_multiple_tickets = models.BooleanField(default=False,
                                                help_text="If unchecked, only the chepeast ticket will be shown.")

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
        # Validate that only one event can be main
        if self.is_main:
            qs = Event.objects.exclude(pk=self.pk).filter(is_main=True)
            if qs.exists():
                raise ValidationError({
                    'is_main': ValidationError(
                        'Only one event can be the main event at a time. Please set the other main event as non-main before saving.',
                        code='not_unique'),
                })
        
        # Auto-generate slug from name if not provided
        if not self.slug and self.name:
            self.slug = slugify(self.name)
            
        return super().clean(*args, **kwargs)

    def tickets_remaining(self):
        from tickets.models import Order

        if self.max_tickets:
            # Only count tickets from ticket types that don't ignore max amount
            tickets_sold = (Order.objects
                            .filter(order_tickets__ticket_type__event=self)
                            .filter(order_tickets__ticket_type__ignore_max_amount=False)
                            .filter(status=Order.OrderStatus.CONFIRMED)
                            .annotate(num_tickets=Sum('order_tickets__quantity'))
                            .aggregate(tickets_sold=Sum('num_tickets'))
                            )['tickets_sold'] or 0
            return self.max_tickets - tickets_sold
        else:
            return 999999999  # extra high number (easy hack)

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
        """Get the main event (displayed at /)"""
        try:
            return cls.objects.get(is_main=True, active=True)
        except cls.DoesNotExist:
            return None

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
    lider = models.ForeignKey(User, on_delete=models.CASCADE, related_name='grupos_liderados', help_text="Usuario líder del grupo")
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
    grupo = models.ForeignKey(Grupo, on_delete=models.CASCADE, related_name='miembros', help_text="Grupo al que pertenece el miembro")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='grupos_miembro', help_text="Usuario miembro del grupo")
    ingreso_anticipado = models.BooleanField(default=False, help_text="Indica si el miembro tiene ingreso anticipado")
    late_checkout = models.BooleanField(default=False, help_text="Indica si el miembro tiene late checkout")

    class Meta:
        verbose_name = "Miembro de Grupo"
        verbose_name_plural = "Miembros de Grupo"
        unique_together = [['grupo', 'user']]
        ordering = ['-ingreso_anticipado', '-late_checkout', 'user__email']

    def clean(self):
        """Valida que el usuario tenga un bono para el evento del grupo"""
        from tickets.models import NewTicket
        
        # No validar si es el líder (se agrega automáticamente)
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
    """Agrega automáticamente al líder como miembro del grupo cuando se crea"""
    if created:
        GrupoMiembro.objects.get_or_create(
            grupo=instance,
            user=instance.lider,
            defaults={'ingreso_anticipado': False}
        )


auditlog.register(Event)
auditlog.register(GrupoTipo)
auditlog.register(Grupo)
auditlog.register(GrupoMiembro)
