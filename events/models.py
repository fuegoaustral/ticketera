from django.db import models
from django.db.models import Q
from django.forms import ValidationError

from auditlog.registry import auditlog

from utils.models import BaseModel


class Event(BaseModel):
    active = models.BooleanField(default=True, help_text="Only 1 event can be active at a time")
    name = models.CharField(max_length=255)
    has_volunteers = models.BooleanField(default=False)
    start = models.DateTimeField()
    end = models.DateTimeField()
    transfers_enabled_until = models.DateTimeField()

    # homepage
    header_image = models.ImageField(upload_to='events/heros', help_text=u"Dimensions: 1666px x 500px")
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
                    'active': ValidationError('Only one event can be active at the same time. Please set the other events as inactive before saving.', code='not_unique'),
                })
        return super().clean(*args, **kwargs)


auditlog.register(Event)
