from django.core.management.base import BaseCommand, CommandError

from events.models import EventRequest
from events.services.event_request_notify import notify_event_request_for_review


class Command(BaseCommand):
    help = 'Reenvía la notificación de revisión de una propuesta (Chatwoot/Slack).'

    def add_arguments(self, parser):
        parser.add_argument('request_id', type=int, nargs='?', default=None)
        parser.add_argument(
            '--all-pending',
            action='store_true',
            help='Reenviar todas las propuestas pending sin conversación Chatwoot',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Reenviar aunque ya tenga conversación (ej. migrar a inbox API)',
        )

    def handle(self, *args, **options):
        if options['all_pending']:
            qs = EventRequest.objects.filter(status=EventRequest.Status.PENDING)
            if not options['force']:
                qs = qs.filter(chatwoot_conversation_id__isnull=True)
        elif options['request_id']:
            qs = EventRequest.objects.filter(pk=options['request_id'])
        else:
            raise CommandError('Pasá request_id o --all-pending')

        if not qs.exists():
            raise CommandError('No hay propuestas para reenviar.')

        for event_request in qs:
            if options['force']:
                event_request.chatwoot_conversation_id = None
                event_request.chatwoot_contact_id = None
                event_request.save(update_fields=[
                    'chatwoot_conversation_id',
                    'chatwoot_contact_id',
                    'updated_at',
                ])
            result = notify_event_request_for_review(event_request)
            channels = []
            if result.chatwoot:
                channels.append(f'Chatwoot {event_request.chatwoot_conversation_id}')
            if result.slack:
                channels.append(f'Slack {event_request.slack_channel}')
            if channels:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Propuesta #{event_request.pk} → ' + ', '.join(channels)
                    )
                )
            else:
                self.stderr.write(
                    self.style.ERROR(f'Propuesta #{event_request.pk} sin ningún canal de notificación')
                )
