from django import forms
from .models import Order, Ticket


class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ('first_name', 'last_name', 'email', 'phone', 'dni')


class OrderForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ('first_name', 'last_name', 'email', 'phone', 'dni')