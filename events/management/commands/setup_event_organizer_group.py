from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from events.models import Event


class Command(BaseCommand):
    help = 'Creates the Event Organizer group with proper permissions'

    def handle(self, *args, **options):
        # Create or get the Event Organizer group
        group, created = Group.objects.get_or_create(name='Event Organizer')
        
        if created:
            self.stdout.write(
                self.style.SUCCESS('Successfully created "Event Organizer" group')
            )
        else:
            self.stdout.write(
                self.style.WARNING('"Event Organizer" group already exists')
            )
        
        # Get the permission for viewing tickets sold report
        content_type = ContentType.objects.get_for_model(Event)
        permission, perm_created = Permission.objects.get_or_create(
            codename='view_tickets_sold_report',
            name='Can view tickets sold report',
            content_type=content_type,
        )
        
        if perm_created:
            self.stdout.write(
                self.style.SUCCESS('Successfully created "view_tickets_sold_report" permission')
            )
        else:
            self.stdout.write(
                self.style.WARNING('"view_tickets_sold_report" permission already exists')
            )
        
        # Add the permission to the group
        group.permissions.add(permission)
        
        self.stdout.write(
            self.style.SUCCESS('Successfully added permission to "Event Organizer" group')
        )
        
        self.stdout.write(
            self.style.SUCCESS(
                'Event Organizer group is ready! Users in this group can now view the tickets sold report.'
            )
        )
