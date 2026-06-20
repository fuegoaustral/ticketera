from django.conf import settings
from django.core.management.base import BaseCommand

from events.services.event_request_chatwoot import (
    _headers,
    _request,
    chatwoot_missing_config,
)


class Command(BaseCommand):
    help = 'Lista inboxes de Chatwoot para configurar CHATWOOT_SOPORTE_INBOX_ID.'

    def handle(self, *args, **options):
        missing = chatwoot_missing_config()
        if missing:
            self.stderr.write(
                self.style.ERROR(
                    'Faltan variables de entorno: ' + ', '.join(missing)
                )
            )
            self.stdout.write(
                '\nCHATWOOT_API_ACCESS_TOKEN → Chatwoot → Profile → Access Token\n'
                'CHATWOOT_ACCOUNT_ID → número en app.chatwoot.com/app/accounts/123/\n'
                'CHATWOOT_SOPORTE_INBOX_ID → Settings → Inboxes → inbox de soporte\n'
            )
            return

        self.stdout.write(
            f'Cuenta {settings.CHATWOOT_ACCOUNT_ID} @ {settings.CHATWOOT_BASE_URL}\n'
        )
        data = _request('GET', '/inboxes')
        if not data:
            self.stderr.write(self.style.ERROR('No se pudo listar inboxes (revisá el token).'))
            return

        payload = data.get('payload') or data
        inboxes = payload if isinstance(payload, list) else [payload]
        if not inboxes:
            self.stdout.write('Sin inboxes.')
            return

        for inbox in inboxes:
            if not isinstance(inbox, dict):
                continue
            self.stdout.write(
                f"- id={inbox.get('id')}  name={inbox.get('name')!r}  "
                f"channel={inbox.get('channel_type')}"
            )
