from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from twilio.rest import Client

from events.models import Event
from .models import Order, Ticket, TicketTransfer, Profile, TicketType


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

    def clean(self):
        cleaned_data = super(ProfileStep1Form, self).clean()
        document_type = cleaned_data.get('document_type')
        document_number = self.clean_document_number()

        # Check for duplicate document number and type
        if Profile.objects.filter(document_type=document_type, document_number=document_number).exclude(
                user=self.instance.user).exists():
            raise forms.ValidationError("Otro usuario ya tiene este tipo de documento y número.")

        return cleaned_data

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
    full_phone_number = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = Profile
        fields = ['phone']

    def __init__(self, *args, **kwargs):
        code_sent = kwargs.pop('code_sent', False)
        super(ProfileStep2Form, self).__init__(*args, **kwargs)

        if code_sent:
            self.fields['phone'].required = False  # Make phone non-required if code is sent
            self.fields['phone'].disabled = True  # Disable the phone field if code is sent

    def clean_phone(self):
        # Use the full_phone_number if provided
        full_phone_number = self.cleaned_data.get('full_phone_number')
        if full_phone_number:
            return full_phone_number
        return self.cleaned_data['phone']

    def clean(self):
        cleaned_data = super(ProfileStep2Form, self).clean()
        phone = cleaned_data.get('phone')

        # Check for duplicate phone number
        if Profile.objects.filter(phone=phone).exclude(user=self.instance.user).exists():
            raise forms.ValidationError("Otro usuario ya tiene este número de teléfono.")

        return cleaned_data

    def send_verification_code(self):
        phone = self.cleaned_data['phone']

        if settings.ENV == 'local' or settings.MOCK_PHONE_VERIFICATION:
            return '123456'

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        verification = client.verify \
            .v2 \
            .services(settings.TWILIO_VERIFY_SERVICE_SID) \
            .verifications \
            .create(to=phone, channel='sms')
        return verification.sid

    def verify_code(self):
        phone = self.cleaned_data['phone']
        code = self.cleaned_data.get('code')

        if settings.ENV == 'local' or settings.MOCK_PHONE_VERIFICATION:
            return True

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        verification_check = client.verify \
            .v2 \
            .services(settings.TWILIO_VERIFY_SERVICE_SID) \
            .verification_checks \
            .create(to=phone, code=code)
        return verification_check.status == 'approved'


class CheckoutTicketSelectionForm(forms.Form):
    def __init__(self, *args, **kwargs):
        initial_data = kwargs.pop('initial', {})
        super(CheckoutTicketSelectionForm, self).__init__(*args, **kwargs)

        ticket_types = TicketType.objects.get_available_ticket_types_for_current_events()

        self.ticket_data = []  # Initialize an empty list to store ticket data

        if ticket_types.exists():
            for ticket_type in ticket_types:
                field_name = f'ticket_{ticket_type.id}_quantity'
                initial_value = initial_data.get(field_name, 0)

                # Create a form field for each ticket type
                self.fields[field_name] = forms.IntegerField(
                    label=f"{ticket_type.name}",
                    min_value=0,
                    max_value=ticket_type.ticket_count,
                    initial=initial_value
                )

                # Store ticket type and price to use in the template
                self.ticket_data.append({
                    'id': ticket_type.id,
                    'name': ticket_type.name,
                    'description': ticket_type.description,
                    'price': ticket_type.price,
                    'field_name': field_name,
                    'initial_quantity': initial_value,  # Pass the initial value to the template
                    'ticket_count': ticket_type.ticket_count
                })
        else:
            self.ticket_data = []


class CheckoutDonationsForm(forms.Form):
    def __init__(self, *args, **kwargs):
        initial_data = kwargs.pop('initial', {})
        super(CheckoutDonationsForm, self).__init__(*args, **kwargs)
        self.fields['donation_art'] = forms.IntegerField(
            label="Becas de Arte",
            min_value=0,
            initial=initial_data.get('donation_art', 0),
            required=False
        )
        self.fields['donation_venue'] = forms.IntegerField(
            label="Donaciones a La Sede",
            min_value=0,
            initial=initial_data.get('donation_venue', 0),
            required=False
        )
        self.fields['donation_grant'] = forms.IntegerField(
            label="Beca Inclusión Radical",
            min_value=0,
            initial=initial_data.get('donation_grant', 0),
            required=False
        )


ORDER_REASON_CHOICES = [
    (Order.OrderType.CASH_ONSITE, 'Pago en efectivo'),
    (Order.OrderType.INTERNATIONAL_TRANSFER, 'Transferencia internacional'),
    (Order.OrderType.LOCAL_TRANSFER, 'Transferencia local'),
    (Order.OrderType.OTHER, 'Otro'),

]

DOCUMENT_TYPE_CHOICES = [
    ('DNI', 'DNI'),
    ('PASSPORT', 'Passport'),
    ('OTHER', 'Other'),
]


class BaseTicketForm(forms.Form):
    first_name = forms.CharField(label='Nombre', required=True)
    last_name = forms.CharField(label='Apellido', required=True)
    document_type = forms.ChoiceField(label='Tipo de documento', required=True, choices=DOCUMENT_TYPE_CHOICES)
    document_number = forms.CharField(label='Número de documento', required=True, max_length=20)
    phone = forms.CharField(label='Teléfono', required=True)
    email = forms.EmailField(label='Email', required=True)
    notes = forms.CharField(label='Notas', required=False, widget=forms.Textarea(attrs={'rows': 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add common widget attributes if needed
        self.fields['first_name'].widget.attrs.update({'placeholder': 'Nombre'})
        self.fields['last_name'].widget.attrs.update({'placeholder': 'Apellido'})
        self.fields['email'].widget.attrs.update({'placeholder': 'Email'})
        self.fields['phone'].widget.attrs.update({'placeholder': 'Teléfono'})

    def clean(self):
        # Shared validation logic (if needed)
        return super().clean()


class TicketPurchaseForm(BaseTicketForm):
    order_type = forms.ChoiceField(label='Tipo de orden', choices=ORDER_REASON_CHOICES, required=True)

    def __init__(self, *args, **kwargs):
        event = kwargs.pop('event', None)
        super().__init__(*args, **kwargs)

        if event:
            ticket_types = TicketType.objects.filter(event=event)
            for ticket in ticket_types:
                self.fields[f'ticket_quantity_{ticket.id}'] = forms.IntegerField(
                    label=f'Bonos tipo {ticket.emoji} {ticket.name}  - ${ticket.price} - Quedan: {ticket.ticket_count}',
                    min_value=0,
                    max_value=ticket.ticket_count,
                    initial=0,
                    required=False,
                )
        self.fields = {
            'order_type': self.fields['order_type'],
            'email': self.fields['email'],
            'first_name': self.fields['first_name'],
            'last_name': self.fields['last_name'],
            'document_type': self.fields['document_type'],
            'document_number': self.fields['document_number'],
            'phone': self.fields['phone'],
            'notes': self.fields['notes'],
            **{field_name: self.fields[field_name] for field_name in self.fields if
               field_name.startswith('ticket_quantity_')},
        }

    def clean(self):
        cleaned_data = super().clean()
        ticket_fields = [key for key in self.fields if key.startswith('ticket_quantity_')]
        total_tickets = 0
        for field in ticket_fields:
            ticket_id = field.split('_')[-1]
            ticket = TicketType.objects.get(id=ticket_id)
            ticket_quantity = cleaned_data.get(field, 0)
            if ticket_quantity > ticket.ticket_count:
                raise ValidationError(f'No hay suficientes tickets de tipo {ticket.name}.')
            total_tickets += ticket_quantity

        if total_tickets == 0:
            raise ValidationError('Debe seleccionar al menos un ticket para continuar con la compra.')

        return cleaned_data
