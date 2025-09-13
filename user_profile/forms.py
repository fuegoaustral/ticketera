from django import forms
from django.conf import settings
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import get_user_model

from twilio.rest import Client

from .models import Profile
from tickets.models import NewTicket

User = get_user_model()


class ProfileStep1Form(forms.ModelForm):
    first_name = forms.CharField(max_length=30, required=True)
    last_name = forms.CharField(max_length=30, required=True)

    class Meta:
        model = Profile
        fields = ["document_type", "document_number"]

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super(ProfileStep1Form, self).__init__(*args, **kwargs)
        if user:
            self.fields["first_name"].initial = user.first_name
            self.fields["last_name"].initial = user.last_name

    def clean_document_number(self):
        document_number = self.cleaned_data.get("document_number", "")
        # Remove periods from the document_number
        cleaned_document_number = document_number.replace(".", "")
        return cleaned_document_number

    def clean(self):
        cleaned_data = super(ProfileStep1Form, self).clean()
        document_type = cleaned_data.get("document_type")
        document_number = self.clean_document_number()

        # Check for duplicate document number and type
        if (
            document_number and
            Profile.objects.filter(
                document_type=document_type, document_number=document_number
            )
            .exclude(user=self.instance.user)
            .exists()
        ):
            raise forms.ValidationError(
                "Otro usuario ya tiene este tipo de documento y número."
            )

        return cleaned_data

    def save(self, commit=True):
        # Create a profile instance, but don't save it yet
        profile = super(ProfileStep1Form, self).save(commit=False)

        # Clean document_number before saving
        profile.document_number = self.clean_document_number()

        # Update the user's first and last name
        user = profile.user
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]

        # If we are saving, also update and save the profile instance
        if commit:
            user.save()
            profile.save()

        return profile


class ProfileStep2Form(forms.ModelForm):
    code = forms.CharField(max_length=6, required=False, label="Código de verificación")
    full_phone_number = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = Profile
        fields = ["phone"]

    def __init__(self, *args, **kwargs):
        code_sent = kwargs.pop("code_sent", False)
        super(ProfileStep2Form, self).__init__(*args, **kwargs)

        if code_sent:
            self.fields["phone"].required = (
                False  # Make phone non-required if code is sent
            )
            self.fields["phone"].disabled = (
                True  # Disable the phone field if code is sent
            )

    def clean_phone(self):
        # Use the full_phone_number if provided
        full_phone_number = self.cleaned_data.get("full_phone_number")
        if full_phone_number:
            return full_phone_number
        return self.cleaned_data["phone"]

    def clean(self):
        cleaned_data = super(ProfileStep2Form, self).clean()
        phone = cleaned_data.get("phone")

        # Check for duplicate phone number
        if (
            Profile.objects.filter(phone=phone)
            .exclude(user=self.instance.user)
            .exists()
        ):
            raise forms.ValidationError(
                "Otro usuario ya tiene este número de teléfono."
            )

        return cleaned_data

    def send_verification_code(self):
        phone = self.cleaned_data["phone"]

        if settings.ENV == "local" or settings.MOCK_PHONE_VERIFICATION:
            return "123456"

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        verification = client.verify.v2.services(
            settings.TWILIO_VERIFY_SERVICE_SID
        ).verifications.create(to=phone, channel="sms")
        return verification.sid

    def verify_code(self):
        phone = self.cleaned_data["phone"]
        code = self.cleaned_data.get("code")

        if settings.ENV == "local" or settings.MOCK_PHONE_VERIFICATION:
            return True

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        verification_check = client.verify.v2.services(
            settings.TWILIO_VERIFY_SERVICE_SID
        ).verification_checks.create(to=phone, code=code)
        return verification_check.status == "approved"


class VolunteeringForm(forms.ModelForm):

    volunteer_ranger = forms.BooleanField(label='Ranger', required=False)
    volunteer_transmutator = forms.BooleanField(label='Transmutadores', required=False)
    volunteer_umpalumpa = forms.BooleanField(label='CAOS (Desarme de la Ciudad)', required=False)

    class Meta:
        model = NewTicket
        fields = ["volunteer_ranger", "volunteer_transmutator", "volunteer_umpalumpa"]


class ProfileUpdateForm(forms.ModelForm):
    """Formulario para actualizar información personal del perfil"""
    first_name = forms.CharField(
        max_length=30, 
        required=True,
        label="Nombre",
        widget=forms.TextInput(attrs={'class': 'form-control', 'autofocus': True})
    )
    last_name = forms.CharField(
        max_length=30, 
        required=True,
        label="Apellido",
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    document_type = forms.ChoiceField(
        choices=Profile.DOCUMENT_TYPE_CHOICES,
        label="Tipo de Documento",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    document_number = forms.CharField(
        max_length=50,
        label="Número de Documento",
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )

    class Meta:
        model = Profile
        fields = ['document_type', 'document_number']

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if user:
            self.fields['first_name'].initial = user.first_name
            self.fields['last_name'].initial = user.last_name

    def clean_document_number(self):
        document_number = self.cleaned_data.get("document_number", "")
        # Remove periods from the document_number
        cleaned_document_number = document_number.replace(".", "")
        return cleaned_document_number

    def clean(self):
        cleaned_data = super().clean()
        
        # Only validate if we have an instance (existing profile)
        if not self.instance or not self.instance.user:
            return cleaned_data
            
        document_type = cleaned_data.get("document_type")
        document_number = cleaned_data.get("document_number", "")
        
        # Clean document number (remove periods)
        if document_number:
            document_number = document_number.replace(".", "")

        # Check for duplicate document number and type only if both are provided and different from current
        if document_number and document_type:
            # Check if the document data is different from current profile
            current_profile = self.instance
            if (current_profile.document_type != document_type or 
                current_profile.document_number != document_number):
                
                existing_profile = Profile.objects.filter(
                    document_type=document_type, 
                    document_number=document_number
                ).exclude(user=self.instance.user).first()
                
                if existing_profile:
                    raise forms.ValidationError(
                        "Otro usuario ya tiene este tipo de documento y número."
                    )


        return cleaned_data

    def save(self, commit=True):
        profile = super().save(commit=False)
        
        # Clean document number before saving
        document_number = self.cleaned_data.get("document_number", "")
        if document_number:
            profile.document_number = document_number.replace(".", "")
        
        # Update the user's first and last name
        user = profile.user
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]

        if commit:
            user.save()
            profile.save()

        return profile


class CustomPasswordChangeForm(forms.Form):
    """Formulario personalizado para cambio de contraseña sin requerir contraseña actual"""
    password1 = forms.CharField(
        label='Nueva contraseña',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Nueva contraseña'
        }),
        min_length=8,
        help_text='Mínimo 8 caracteres'
    )
    
    password2 = forms.CharField(
        label='Confirmar nueva contraseña',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Confirmar nueva contraseña'
        })
    )
    
    def clean_password2(self):
        password1 = self.cleaned_data.get('password1')
        password2 = self.cleaned_data.get('password2')
        
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError('Las contraseñas no coinciden.')
        
        return password2
    
    def clean_password1(self):
        password1 = self.cleaned_data.get('password1')
        
        if password1 and len(password1) < 8:
            raise forms.ValidationError('La contraseña debe tener al menos 8 caracteres.')
        
        return password1


class AddEmailForm(forms.Form):
    """Formulario para agregar un nuevo email"""
    email = forms.EmailField(
        label="Nuevo email",
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'nuevo@email.com'})
    )


class PhoneUpdateForm(forms.ModelForm):
    """Formulario para actualizar el teléfono con verificación SMS"""
    code = forms.CharField(
        max_length=6, 
        required=False, 
        label="Código de verificación",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '123456'})
    )
    full_phone_number = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = Profile
        fields = ["phone"]
        widgets = {
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+54 11 1234 5678'})
        }

    def __init__(self, *args, **kwargs):
        code_sent = kwargs.pop("code_sent", False)
        super().__init__(*args, **kwargs)

        if code_sent:
            self.fields["phone"].required = False
            self.fields["phone"].disabled = True

    def clean_phone(self):
        # Use the full_phone_number if provided
        full_phone_number = self.cleaned_data.get("full_phone_number")
        if full_phone_number:
            return full_phone_number
        return self.cleaned_data["phone"]

    def clean(self):
        cleaned_data = super().clean()
        phone = cleaned_data.get("phone")

        # Check for duplicate phone number only if different from current
        if phone and phone != self.instance.phone:
            existing_phone = Profile.objects.filter(phone=phone).exclude(user=self.instance.user).first()
            if existing_phone:
                raise forms.ValidationError(
                    "Otro usuario ya tiene este número de teléfono."
                )

        return cleaned_data

    def send_verification_code(self):
        phone = self.cleaned_data["phone"]

        if settings.ENV == "local" or settings.MOCK_PHONE_VERIFICATION:
            return "123456"

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        verification = client.verify.v2.services(
            settings.TWILIO_VERIFY_SERVICE_SID
        ).verifications.create(to=phone, channel="sms")
        return verification.sid

    def verify_code(self):
        phone = self.cleaned_data["phone"]
        code = self.cleaned_data.get("code")

        if settings.ENV == "local" or settings.MOCK_PHONE_VERIFICATION:
            return True

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        verification_check = client.verify.v2.services(
            settings.TWILIO_VERIFY_SERVICE_SID
        ).verification_checks.create(to=phone, code=code)
        return verification_check.status == "approved"
