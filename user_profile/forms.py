from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordChangeForm
from django.conf import settings
from events.models import Event
from tickets.models import TicketType, NewTicket, Order
from .models import Profile
from twilio.rest import Client
from twilio.base.exceptions import TwilioException

User = get_user_model()


class CajaEmitirBonoForm(forms.Form):
    """Formulario para emitir bonos desde caja"""
    
    PAYMENT_METHODS = [
        ('efectivo', 'Efectivo'),
        ('transferencia', 'Transferencia'),
        ('transferencia_internacional', 'Transferencia Internacional'),
    ]
    
    ticket_type = forms.ModelChoiceField(
        queryset=TicketType.objects.none(),
        label="Tipo de Bono",
        required=False,  # Hacer opcional para el nuevo sistema
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    
    quantity = forms.IntegerField(
        label="Cantidad",
        min_value=1,
        max_value=10,
        initial=1,
        required=False,  # Hacer opcional para el nuevo sistema
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    
    payment_method = forms.ChoiceField(
        choices=PAYMENT_METHODS,
        label="Forma de Pago",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    
    email = forms.EmailField(
        label="Email (Opcional)",
        required=False,
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'usuario@ejemplo.com'})
    )
    
    mark_as_used = forms.BooleanField(
        label="Marcar como usada (venta en puerta)",
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    
    def __init__(self, event, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filtrar tipos de ticket solo para este evento y que estén marcados para mostrar en caja
        # Si show_in_caja=True, ignorar las fechas desde y hasta
        from django.utils import timezone
        from django.db.models import Q
        
        self.fields['ticket_type'].queryset = TicketType.objects.filter(
            event=event, 
            show_in_caja=True
        ).filter(
            Q(ticket_count__gt=0) | Q(ticket_count__isnull=True)
        )
    
    def clean_email(self):
        email = self.cleaned_data.get('email')
        # No validamos si el usuario ya tiene bonos aquí
        # La lógica de owner vs holder se maneja en la vista
        return email
    
    def clean_quantity(self):
        quantity = self.cleaned_data.get('quantity')
        if quantity and quantity > 10:
            raise forms.ValidationError("No se pueden emitir más de 10 bonos a la vez.")
        return quantity
    
    def clean(self):
        cleaned_data = super().clean()
        ticket_type = cleaned_data.get('ticket_type')
        quantity = cleaned_data.get('quantity')
        
        # Verificar que al menos uno de los sistemas tenga datos
        # (esto se validará en la vista con los datos POST)
        return cleaned_data


class ProfileStep1Form(forms.ModelForm):
    """Formulario para el paso 1 del perfil"""
    first_name = forms.CharField(max_length=30, widget=forms.TextInput(attrs={'class': 'form-control'}))
    last_name = forms.CharField(max_length=30, widget=forms.TextInput(attrs={'class': 'form-control'}))
    
    class Meta:
        model = Profile
        fields = ['document_type', 'document_number']
        widgets = {
            'document_type': forms.Select(attrs={'class': 'form-select'}),
            'document_number': forms.TextInput(attrs={'class': 'form-control'}),
        }
    
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if self.user:
            self.fields['first_name'].initial = self.user.first_name
            self.fields['last_name'].initial = self.user.last_name
    
    def save(self, commit=True):
        profile = super().save(commit=False)
        if self.user:
            self.user.first_name = self.cleaned_data['first_name']
            self.user.last_name = self.cleaned_data['last_name']
            if commit:
                self.user.save()
        if commit:
            profile.save()
        return profile


class ProfileStep2Form(forms.ModelForm):
    """Formulario para el paso 2 del perfil"""
    code = forms.CharField(
        max_length=6,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ingresa el código'}),
        label='Código de verificación'
    )
    
    class Meta:
        model = Profile
        fields = ['phone']
        widgets = {
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
        }
    
    def __init__(self, *args, **kwargs):
        self.code_sent = kwargs.pop('code_sent', False)
        super().__init__(*args, **kwargs)
        
        # Only show code field if code was sent
        if not self.code_sent:
            self.fields.pop('code', None)
    
    def send_verification_code(self):
        """Send SMS verification code using Twilio"""
        if not self.instance.phone:
            raise ValueError("Phone number is required")
        
        # Check if phone verification is mocked
        if getattr(settings, 'MOCK_PHONE_VERIFICATION', False):
            # In mock mode, just return a mock verification object
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"MOCK MODE: SMS verification code would be sent to {self.instance.phone}")
            return type('MockVerification', (), {'status': 'pending'})()
        
        try:
            client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
            verification = client.verify.v2.services(settings.TWILIO_VERIFY_SERVICE_SID) \
                .verifications \
                .create(to=self.instance.phone, channel='sms')
            return verification
        except TwilioException as e:
            raise Exception(f"Error sending verification code: {str(e)}")
    
    def verify_code(self):
        """Verify SMS code using Twilio"""
        code = self.cleaned_data.get('code')
        if not code:
            return False
        
        # Check if phone verification is mocked
        if getattr(settings, 'MOCK_PHONE_VERIFICATION', False):
            # In mock mode, accept any 6-digit code
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"MOCK MODE: Verifying code {code} for {self.instance.phone}")
            return len(code) == 6 and code.isdigit()
        
        try:
            client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
            verification_check = client.verify.v2.services(settings.TWILIO_VERIFY_SERVICE_SID) \
                .verification_checks \
                .create(to=self.instance.phone, code=code)
            return verification_check.status == 'approved'
        except TwilioException as e:
            raise Exception(f"Error verifying code: {str(e)}")


class VolunteeringForm(forms.ModelForm):
    """Formulario para voluntariado"""
    class Meta:
        model = NewTicket
        fields = ['volunteer_ranger', 'volunteer_transmutator', 'volunteer_umpalumpa']
        widgets = {
            'volunteer_ranger': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'volunteer_transmutator': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'volunteer_umpalumpa': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class ProfileUpdateForm(forms.ModelForm):
    """Formulario para actualizar perfil"""
    first_name = forms.CharField(max_length=30, widget=forms.TextInput(attrs={'class': 'form-control'}))
    last_name = forms.CharField(max_length=30, widget=forms.TextInput(attrs={'class': 'form-control'}))
    
    class Meta:
        model = Profile
        fields = ['document_type', 'document_number', 'phone']
        widgets = {
            'document_type': forms.Select(attrs={'class': 'form-select'}),
            'document_number': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
        }
    
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if self.user:
            self.fields['first_name'].initial = self.user.first_name
            self.fields['last_name'].initial = self.user.last_name
    
    def save(self, commit=True):
        profile = super().save(commit=False)
        if self.user:
            self.user.first_name = self.cleaned_data['first_name']
            self.user.last_name = self.cleaned_data['last_name']
            if commit:
                self.user.save()
        if commit:
            profile.save()
        return profile


class CustomPasswordChangeForm(forms.Form):
    """Formulario personalizado para cambio de contraseña"""
    password1 = forms.CharField(
        label="Nueva Contraseña",
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Nueva contraseña'}),
        help_text="Su contraseña debe contener por lo menos 8 caracteres."
    )
    password2 = forms.CharField(
        label="Confirmar Nueva Contraseña",
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Confirmar nueva contraseña'})
    )
    
    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
    
    def clean_password2(self):
        password1 = self.cleaned_data.get('password1')
        password2 = self.cleaned_data.get('password2')
        if password1 and password2:
            if password1 != password2:
                raise forms.ValidationError("Las contraseñas no coinciden.")
        return password2
    
    def clean_password1(self):
        password1 = self.cleaned_data.get('password1')
        if password1:
            # Validar que la contraseña cumple con los requisitos
            if len(password1) < 8:
                raise forms.ValidationError("La contraseña debe tener al menos 8 caracteres.")
        return password1
    
    def save(self):
        password = self.cleaned_data['password1']
        self.user.set_password(password)
        self.user.save()
        return self.user


class AddEmailForm(forms.Form):
    """Formulario para agregar email"""
    email = forms.EmailField(
        label="Nuevo Email",
        widget=forms.EmailInput(attrs={'class': 'form-control'})
    )


class PhoneUpdateForm(forms.ModelForm):
    """Formulario para actualizar teléfono"""
    code = forms.CharField(
        max_length=6,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ingresa el código'}),
        label='Código de verificación'
    )
    
    class Meta:
        model = Profile
        fields = ['phone']
        widgets = {
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
        }
    
    def __init__(self, *args, **kwargs):
        self.code_sent = kwargs.pop('code_sent', False)
        super().__init__(*args, **kwargs)
        
        # Only show code field if code was sent
        if not self.code_sent:
            self.fields.pop('code', None)
    
    def send_verification_code(self):
        """Send SMS verification code using Twilio"""
        phone = self.cleaned_data.get('phone')
        if not phone:
            raise ValueError("Phone number is required")
        
        # Check if phone verification is mocked
        if getattr(settings, 'MOCK_PHONE_VERIFICATION', False):
            # In mock mode, just return a mock verification object
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"MOCK MODE: SMS verification code would be sent to {phone}")
            return type('MockVerification', (), {'status': 'pending'})()
        
        try:
            client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
            verification = client.verify.v2.services(settings.TWILIO_VERIFY_SERVICE_SID) \
                .verifications \
                .create(to=phone, channel='sms')
            return verification
        except TwilioException as e:
            raise Exception(f"Error sending verification code: {str(e)}")
    
    def verify_code(self):
        """Verify SMS code using Twilio"""
        code = self.cleaned_data.get('code')
        phone = self.cleaned_data.get('phone')
        if not code or not phone:
            return False
        
        # Check if phone verification is mocked
        if getattr(settings, 'MOCK_PHONE_VERIFICATION', False):
            # In mock mode, accept any 6-digit code
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"MOCK MODE: Verifying code {code} for {phone}")
            return len(code) == 6 and code.isdigit()
        
        try:
            client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
            verification_check = client.verify.v2.services(settings.TWILIO_VERIFY_SERVICE_SID) \
                .verification_checks \
                .create(to=phone, code=code)
            return verification_check.status == 'approved'
        except TwilioException as e:
            raise Exception(f"Error verifying code: {str(e)}")