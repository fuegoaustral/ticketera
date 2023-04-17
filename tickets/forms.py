from django import forms

from .models import Order, Ticket, TicketTransfer
from events.models import Event


class PersonForm(forms.ModelForm):
    first_name = forms.CharField(label='', widget=forms.TextInput(attrs={'class': 'input-first_name', 'placeholder': 'Nombre'}))
    last_name = forms.CharField(label='', widget=forms.TextInput(attrs={'class': 'input-last_name', 'placeholder': 'Apellido'}))
    email = forms.EmailField(label='', widget=forms.EmailInput(attrs={'class': 'input-email', 'placeholder': 'Email'}))
    phone = forms.CharField(label='', widget=forms.TextInput(attrs={'class': 'input-phone', 'placeholder': 'Teléfono'}))
    dni = forms.CharField(label='', widget=forms.TextInput(attrs={'class': 'input-dni', 'placeholder': 'DNI o Pasaporte'}))


class TicketForm(PersonForm):
    volunteer = forms.ChoiceField(label='Voluntariado', widget=forms.RadioSelect(attrs={'class': 'input-volunteer',}), choices=Ticket.VOLUNTEER_CHOICES)
    # volunteer_ranger = forms.BooleanField(label='Ranger', required=False)
    # volunteer_transmutator = forms.BooleanField(label='Transmutadores', required=False)
    # volunteer_umpalumpa = forms.BooleanField(label='Umpa Lumpas (armado y desarme de la ciudad)', required=False)

    class Meta:
        model = Ticket
        fields = ('first_name', 'last_name', 'email', 'phone', 'dni', 'volunteer', 'volunteer_ranger', 'volunteer_transmutator', 'volunteer_umpalumpa')
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
        fields = ('first_name', 'last_name', 'email', 'phone', 'dni', 'donation_art', 'donation_grant', 'donation_venue',)


class TransferForm(PersonForm):
    class Meta:
        model = TicketTransfer
        fields = ('first_name', 'last_name', 'email', 'phone', 'dni', )
