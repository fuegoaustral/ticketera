from django import forms
from .models import Order, Ticket

class PersonForm(forms.ModelForm):
    first_name = forms.CharField(label='', widget=forms.TextInput(attrs={'class': 'input-first_name', 'placeholder': 'Nombre'}))
    last_name = forms.CharField(label='', widget=forms.TextInput(attrs={'class': 'input-last_name', 'placeholder': 'Apellido'}))
    email = forms.EmailField(label='', widget=forms.EmailInput(attrs={'class': 'input-email', 'placeholder': 'Email'}))
    phone = forms.CharField(label='', widget=forms.TextInput(attrs={'class': 'input-phone', 'placeholder': 'Teléfono'}))
    dni = forms.CharField(label='', widget=forms.TextInput(attrs={'class': 'input-dni', 'placeholder': 'DNI'}))

class TicketForm(PersonForm):
    class Meta:
        model = Ticket
        fields = ('first_name', 'last_name', 'email', 'phone', 'dni')


class OrderForm(PersonForm):
    class Meta:
        model = Order
        fields = ('first_name', 'last_name', 'email', 'phone', 'dni')