from django.db import models
from django.db.models import Count, Sum, Q
from django.forms import ValidationError

from auditlog.registry import auditlog

from utils.models import BaseModel


class Event(BaseModel):
    active = models.BooleanField(default=True, help_text="Only 1 event can be active at a time")
    name = models.CharField(max_length=255)
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


auditlog.register(Event)
