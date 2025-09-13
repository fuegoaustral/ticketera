from django.core.management.base import BaseCommand
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount


class Command(BaseCommand):
    help = 'Migra emails de Google que no tienen EmailAddress asociado'

    def handle(self, *args, **options):
        google_accounts = SocialAccount.objects.filter(provider='google')
        created_count = 0
        
        for social_account in google_accounts:
            if social_account.extra_data.get('email'):
                email = social_account.extra_data['email'].lower()
                user = social_account.user
                
                # Verificar si ya existe un EmailAddress para este email
                if not EmailAddress.objects.filter(user=user, email=email).exists():
                    EmailAddress.objects.create(
                        user=user,
                        email=email,
                        verified=True,  # Los emails de Google se consideran verificados
                        primary=False  # No hacer principal automáticamente
                    )
                    created_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(f'Creado EmailAddress para {email} (usuario: {user.email})')
                    )
        
        self.stdout.write(
            self.style.SUCCESS(f'Migración completada. Se crearon {created_count} EmailAddress.')
        )
