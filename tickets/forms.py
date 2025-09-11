from django import forms
from django.core.exceptions import ValidationError

from events.models import Event
from .models import Order, Ticket, TicketTransfer, TicketType


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
        # Try to get event from the form's instance or use main event
        event = None
        if hasattr(self, 'instance') and self.instance and hasattr(self.instance, 'order') and self.instance.order:
            event = self.instance.order.ticket_type.event
        if not event:
            event = Event.get_main_event()
        if event and not event.has_volunteers:
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


class CheckoutTicketSelectionForm(forms.Form):
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        self.event = kwargs.pop('event', None)
        initial_data = kwargs.pop('initial', {})
        super(CheckoutTicketSelectionForm, self).__init__(*args, **kwargs)

        # Get ticket types for the specific event
        if self.event:
            # Use the same logic as get_available_ticket_types_for_current_events but for specific event
            from django.utils import timezone
            from django.db.models import Q
            ticket_types = (TicketType.objects
                          .filter(event=self.event)
                          .filter(Q(date_from__lte=timezone.now()) | Q(date_from__isnull=True))
                          .filter(Q(date_to__gte=timezone.now()) | Q(date_to__isnull=True))
                          .filter(Q(ticket_count__gt=0) | Q(ticket_count__isnull=True))
                          .filter(is_direct_type=False)
                          .order_by('cardinality', 'price'))
        else:
            # Fallback to current events if no specific event
            ticket_types = TicketType.objects.get_available_ticket_types_for_current_events().order_by('cardinality', 'price')

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
                    'quantity': initial_value,  # Pass the initial value to the template
                    'ticket_count': ticket_type.ticket_count
                })
        else:
            self.ticket_data = []

    def clean(self):
        cleaned_data = super().clean()

        # check if any total selected tickets quantity is greater than available tickets
        if self.event:
            event = self.event
        else:
            event = Event.get_main_event()
        tickets_remaining = event.tickets_remaining() or 0
        available_tickets = event.max_tickets_per_order
        available_tickets = min(available_tickets, tickets_remaining)
        total_selected_tickets = sum(cleaned_data.get(field, 0) for field in self.fields if field.startswith('ticket_'))
        if total_selected_tickets > available_tickets:
            # merge cleaned_data values with ticket_data quantity
            self.ticket_data = [
                {**ticket, 'quantity': cleaned_data.get(f'ticket_{ticket["id"]}_quantity', ticket['quantity'])}
                for ticket in self.ticket_data
            ]
            raise ValidationError(f'Superaste la máxima cantidad de bonos disponibles para esta compra: {available_tickets}.')

        # check if there's any ticket quantity greater than zero
        if all(cleaned_data.get(field, 0) == 0 for field in self.fields if field.startswith('ticket_')):
            raise ValidationError('Debe seleccionar al menos un ticket para continuar con la compra.')

        return cleaned_data


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
            if 0 < ticket.ticket_count < ticket_quantity:
                raise ValidationError(f'No hay suficientes tickets de tipo {ticket.name}.')
            total_tickets += ticket_quantity

        if total_tickets == 0:
            raise ValidationError('Debe seleccionar al menos un ticket para continuar con la compra.')

        return cleaned_data
