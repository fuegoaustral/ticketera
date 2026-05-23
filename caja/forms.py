from decimal import Decimal

from django import forms
from django.forms import ModelForm

from caja.models import EventCaja, EventCajaMercadoPagoConfig, EventProduct
from tickets.models import TicketType


class EventProductForm(ModelForm):
    class Meta:
        model = EventProduct
        fields = ['name', 'price', 'is_active', 'ticket_type']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'ticket_type': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        self.has_unlinked_tickets = False
        if event:
            linked = TicketType.objects.filter(
                event=event,
                show_in_caja=True,
            ).filter(event_product__isnull=True)
            if self.instance and self.instance.pk and self.instance.ticket_type_id:
                linked = linked | TicketType.objects.filter(pk=self.instance.ticket_type_id)
            self.has_unlinked_tickets = linked.exists()
            if self.has_unlinked_tickets:
                self.fields['ticket_type'].queryset = linked
                self.fields['ticket_type'].required = False
                self.fields['ticket_type'].empty_label = '— Producto genérico —'
            else:
                self.fields.pop('ticket_type', None)
                self.fields['name'].required = True
                self.fields['price'].required = True
                self.fields['name'].widget.attrs.setdefault(
                    'placeholder',
                    'Ej: Cerveza, Remera, Comida',
                )

    def clean(self):
        cleaned = super().clean()
        ticket_type = cleaned.get('ticket_type')
        if ticket_type:
            if self.event and ticket_type.event_id != self.event.id:
                raise forms.ValidationError('El tipo de bono no pertenece a este evento.')
            return cleaned
        if not cleaned.get('name'):
            raise forms.ValidationError('Indicá un nombre para el producto genérico.')
        if cleaned.get('price') in (None, ''):
            raise forms.ValidationError('Los productos genéricos requieren un precio.')
        return cleaned


class EventProductCreateForm(EventProductForm):
    class Meta(EventProductForm.Meta):
        fields = ['name', 'price', 'ticket_type']

    unlimited_stock = forms.BooleanField(
        required=False,
        label='Stock ilimitado',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_unlimited_stock'}),
    )
    initial_stock = forms.IntegerField(
        required=False,
        min_value=0,
        label='Stock inicial',
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'min': '0',
            'placeholder': '0',
            'id': 'id_initial_stock',
        }),
    )

    def save(self, commit=True):
        product = super().save(commit=False)
        product.is_active = True
        if commit:
            product.save()
        return product


class EventProductEditForm(EventProductForm):
    unlimited_stock = forms.BooleanField(
        required=False,
        label='Stock ilimitado',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_unlimited_stock'}),
    )

    def __init__(self, *args, stock_unlimited=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['unlimited_stock'].initial = stock_unlimited


class StockQuantityForm(forms.Form):
    quantity = forms.IntegerField(
        min_value=1,
        label='Cantidad',
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
    )
    notes = forms.CharField(
        required=False,
        label='Notas',
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Opcional'}),
    )


class EventCajaForm(ModelForm):
    class Meta:
        model = EventCaja
        fields = ['name', 'is_active', 'sort_order']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'sort_order': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
        }


class EventCajaCreateForm(ModelForm):
    class Meta:
        model = EventCaja
        fields = ['name']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Nombre de la caja'}),
        }


class EventCajaMercadoPagoConfigForm(ModelForm):
    class Meta:
        model = EventCajaMercadoPagoConfig
        fields = [
            'external_store_id',
            'external_pos_id',
            'store_id',
            'pos_id',
            'terminal_id',
        ]
        widgets = {
            'external_store_id': forms.TextInput(attrs={'class': 'form-control'}),
            'external_pos_id': forms.TextInput(attrs={'class': 'form-control'}),
            'store_id': forms.NumberInput(attrs={'class': 'form-control'}),
            'pos_id': forms.NumberInput(attrs={'class': 'form-control'}),
            'terminal_id': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'NEWLAND_N950__...'}),
        }


class StockAdjustForm(forms.Form):
    delta = forms.IntegerField(
        required=False,
        label='Cantidad (+/-)',
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
    )
    unlimited = forms.BooleanField(
        required=False,
        label='Stock ilimitado',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
