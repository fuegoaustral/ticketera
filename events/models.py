from django.db import models

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

    def clean(*args, **kwargs):
        # TODO: check that only one is active
        pass


auditlog.register(Event)
