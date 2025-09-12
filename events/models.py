from django.db import models
from django.db.models import Count, Sum, Q
from django.forms import ValidationError
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
    header_bg_color = models.CharField(max_length=7,
                                       help_text='e.g. "#fc0006". The color of the background to fill on bigger screens.')
    title = models.TextField()
    description = models.TextField()

    # ticket form
    pre_ticket_form_info = models.TextField(help_text=u'Will appear before the buy ticket form')

    # ticket email
    email_info = models.TextField()

    attendee_must_be_registered = models.BooleanField(default=True, help_text="If checked, all attendees must be registered users")
    
    admins = models.ManyToManyField(User, blank=True, related_name='admin_events', help_text="Users who can administer this event")

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
            tickets_sold = (Order.objects
                            .filter(order_tickets__ticket_type__event=self)
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


auditlog.register(Event)
