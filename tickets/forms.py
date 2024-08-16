from django import forms

from .models import Order, Ticket, TicketTransfer, Profile
from events.models import Event
from twilio.rest import Client
from django.conf import settings


class PersonForm(forms.ModelForm):
    first_name = forms.CharField(label='',
                                 widget=forms.TextInput(attrs={'class': 'input-first_name', 'placeholder': 'Nombre'}))
    last_name = forms.CharField(label='',
                                widget=forms.TextInput(attrs={'class': 'input-last_name', 'placeholder': 'Apellido'}))
    email = forms.EmailField(label='', widget=forms.EmailInput(attrs={'class': 'input-email', 'placeholder': 'Email'}))
    phone = forms.CharField(label='', widget=forms.TextInput(attrs={'class': 'input-phone', 'placeholder': 'Teléfono'}))
    dni = forms.CharField(label='',
                          widget=forms.TextInput(attrs={'class': 'input-dni', 'placeholder': 'DNI o Pasaporte'}))


class TicketForm(PersonForm):
    volunteer = forms.ChoiceField(label='Voluntariado', widget=forms.RadioSelect(attrs={'class': 'input-volunteer', }),
                                  choices=Ticket.VOLUNTEER_CHOICES)
    volunteer_ranger = forms.BooleanField(label='Ranger', required=False)
    volunteer_transmutator = forms.BooleanField(label='Transmutadores', required=False)
    volunteer_umpalumpa = forms.BooleanField(label='CAOS (Desarme de la Ciudad)', required=False)

    class Meta:
        model = Ticket
        fields = (
            'first_name', 'last_name', 'email', 'phone', 'dni', 'volunteer', 'volunteer_ranger',
            'volunteer_transmutator',
            'volunteer_umpalumpa')
        widgets = {
            'volunteer': forms.RadioSelect
        }

    def __init__(self, *args, **kwargs):
        super(TicketForm, self).__init__(*args, **kwargs)
        # ugly hack to disable volunteers
        event = Event.objects.get(active=True)
        if not event.has_volunteers:
            self.initial['volunteer'] = 'no'

    def clean(self):
        super().clean()
        volunteer = self.cleaned_data.get("volunteer")
        if volunteer == 'yes':
            volunteer_ranger = self.cleaned_data.get("volunteer_ranger")
            volunteer_transmutator = self.cleaned_data.get("volunteer_transmutator")
            volunteer_umpalumpa = self.cleaned_data.get("volunteer_umpalumpa")
            if not volunteer_ranger and not volunteer_transmutator and not volunteer_umpalumpa:
                self.add_error('volunteer', 'Indicar el tipo de voluntariado')


class OrderForm(PersonForm):
    class Meta:
        model = Order
        fields = (
            'first_name', 'last_name', 'email', 'phone', 'dni', 'donation_art', 'donation_grant', 'donation_venue',)


class TransferForm(PersonForm):
    class Meta:
        model = TicketTransfer
        fields = ('first_name', 'last_name', 'email', 'phone', 'dni',)


class ProfileStep1Form(forms.ModelForm):
    first_name = forms.CharField(max_length=30, required=True)
    last_name = forms.CharField(max_length=30, required=True)

    class Meta:
        model = Profile
        fields = ['document_type', 'document_number']

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super(ProfileStep1Form, self).__init__(*args, **kwargs)
        if user:
            self.fields['first_name'].initial = user.first_name
            self.fields['last_name'].initial = user.last_name

    def clean_document_number(self):
        document_number = self.cleaned_data.get('document_number', '')
        # Remove periods from the document_number
        cleaned_document_number = document_number.replace('.', '')
        return cleaned_document_number

    def save(self, commit=True):
        # Create a profile instance, but don't save it yet
        profile = super(ProfileStep1Form, self).save(commit=False)

        # Clean document_number before saving
        profile.document_number = self.clean_document_number()

        # Update the user's first and last name
        user = profile.user
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']

        # If we are saving, also update and save the profile instance
        if commit:
            user.save()
            profile.save()

        return profile


class ProfileStep2Form(forms.ModelForm):
    code = forms.CharField(max_length=6, required=False, label="Código de verificación")

    class Meta:
        model = Profile
        fields = ['phone']

    def __init__(self, *args, **kwargs):
        code_sent = kwargs.pop('code_sent', False)
        super(ProfileStep2Form, self).__init__(*args, **kwargs)

        if code_sent:
            self.fields['phone'].required = False  # Make phone non-required if code is sent
            self.fields['phone'].disabled = True  # Disable the phone field if code is sent

    def send_verification_code(self):
        phone = self.cleaned_data['phone']
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        verification = client.verify \
            .v2 \
            .services(settings.TWILIO_VERIFY_SERVICE_SID) \
            .verifications \
            .create(to=phone, channel='sms')
        return "verification.sid"

    def verify_code(self):
        phone = self.cleaned_data['phone']
        code = self.cleaned_data.get('code')
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        verification_check = client.verify \
            .v2 \
            .services(settings.TWILIO_VERIFY_SERVICE_SID) \
            .verification_checks \
            .create(to=phone, code=code)
        return verification_check.status == 'approved'
