import uuid

from django.contrib.auth.models import Group, Permission
from django.contrib.auth.models import User
from django.db.models.signals import post_save, pre_save, post_migrate
from django.dispatch import receiver

from .models import Profile
from .groups import GROUPS_PERMISSIONS


@receiver(pre_save, sender=User)
def set_username(sender, instance, **kwargs):
    if not instance.username:
        instance.username = instance.email


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created and not instance.is_staff and not hasattr(instance, 'profile'):
        Profile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    if not instance.is_staff:
        instance.profile.save()

@receiver(post_migrate)
def create_user_groups(sender, **kwargs):
    """
    Creates user groups and assigns permissions after migrations.
    """

    for group_name, perms in GROUPS_PERMISSIONS.items():
        group, created = Group.objects.get_or_create(name=group_name)
        if created:
            print(f"Created group: {group_name}")

        permission_objects = []
        for perm in perms:
            try:
                permission = Permission.objects.get(
                    codename=perm['codename'],
                    content_type__app_label=perm['app_label']
                )
                permission_objects.append(permission)
            except Permission.DoesNotExist:
                print(f"Permission {perm['codename']} in app {perm['app_label']} does not exist.")

        # Assign permissions to the group
        group.permissions.set(permission_objects)
        group.save()