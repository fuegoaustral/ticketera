from django.db.models.signals import post_save
from django.dispatch import receiver

from events.models import Event
from events.services.main_event import promote_if_no_valid_main, reconcile_main_event


@receiver(post_save, sender=Event)
def handle_event_main_status(sender, instance, created, **kwargs):
    if created:
        promote_if_no_valid_main(instance)
    else:
        reconcile_main_event()
