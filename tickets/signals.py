import uuid

from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.db.models.signals import pre_save

from django.dispatch import receiver

from tickets.models import Profile


@receiver(pre_save, sender=User)
def set_uuid_for_username(sender, instance, **kwargs):
    if not instance.username:
        instance.username = str(uuid.uuid4())
@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created and not instance.is_staff:
        Profile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    if not instance.is_staff:
        instance.profile.save()