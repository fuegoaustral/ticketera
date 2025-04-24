from django.db import models
from django.db.models import Count, Sum, Q
from django.forms import ValidationError
from django.utils import timezone

from auditlog.registry import auditlog

from utils.models import BaseModel


class Event(BaseModel):
    active = models.BooleanField(default=True, help_text="Only 1 event can be active at a time")
    name = models.CharField(max_length=255)
    location = models.CharField(max_length=255, blank=True, help_text="Location of the event")
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

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['active'], condition=Q(active=True), name='unique_active')
        ]

    def __str__(self):
        return self.name

    def clean(self, *args, **kwargs):
        if self.active:
            qs = Event.objects.exclude(pk=self.pk).filter(active=True)
            if qs.exists():
                raise ValidationError({
                    'active': ValidationError(
                        'Only one event can be active at the same time. Please set the other events as inactive before saving.',
                        code='not_unique'),
                })
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


auditlog.register(Event)
