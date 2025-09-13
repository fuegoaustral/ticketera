from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.account.models import EmailAddress
from django.conf import settings


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Adapter personalizado para manejar redirecciones de cuentas sociales
    """
    
    def get_connect_redirect_url(self, request, socialaccount):
        """
        Redirige a la página de emails después de conectar una cuenta social
        """
        return '/mi-fuego/email/'
    
    def get_login_redirect_url(self, request):
        """
        Redirige a la página principal después del login social
        """
        return '/mi-fuego/'
    
    def save_user(self, request, sociallogin, form=None):
        """
        Sobrescribe el método para crear EmailAddress automáticamente
        """
        user = super().save_user(request, sociallogin, form)
        
        # Crear EmailAddress para el email de la cuenta social si no existe
        if sociallogin.account.extra_data.get('email'):
            email = sociallogin.account.extra_data['email'].lower()
            if not EmailAddress.objects.filter(user=user, email=email).exists():
                EmailAddress.objects.create(
                    user=user,
                    email=email,
                    verified=True,  # Los emails de Google se consideran verificados
                    primary=False  # No hacer principal automáticamente
                )
        
        return user
    
    def pre_social_login(self, request, sociallogin):
        """
        Se ejecuta antes del login social para crear EmailAddress si es necesario
        """
        if sociallogin.account.extra_data.get('email'):
            email = sociallogin.account.extra_data['email'].lower()
            user = sociallogin.user
            
            if user and not EmailAddress.objects.filter(user=user, email=email).exists():
                EmailAddress.objects.create(
                    user=user,
                    email=email,
                    verified=True,
                    primary=False
                )
