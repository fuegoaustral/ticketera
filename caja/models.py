from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models

from events.models import Event
from tickets.models import Order, TicketType
from utils.models import BaseModel


class EventProduct(BaseModel):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='event_products')
    ticket_type = models.OneToOneField(
        TicketType,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='event_product',
    )
    name = models.CharField(max_length=200, blank=True)
    price = models.DecimalField(decimal_places=2, max_digits=10, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name', 'id']

    def clean(self):
        if self.ticket_type_id:
            if self.ticket_type.event_id != self.event_id:
                raise ValidationError('El tipo de bono debe pertenecer al mismo evento.')
            if not self.ticket_type.show_in_caja:
                raise ValidationError('El tipo de bono debe tener "Mostrar en Caja" activado.')
        else:
            if not self.name:
                raise ValidationError('Los productos genéricos requieren un nombre.')
            if self.price is None:
                raise ValidationError('Los productos genéricos requieren un precio.')

    def save(self, *args, **kwargs):
        if self.ticket_type_id:
            if not self.name:
                self.name = self.ticket_type.name
            if self.price is None:
                self.price = self.ticket_type.price or Decimal('0')
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def display_name(self):
        return self.name or (self.ticket_type.name if self.ticket_type_id else '')

    @property
    def is_ticket_product(self):
        return self.ticket_type_id is not None

    def __str__(self):
        return f'{self.display_name} ({self.event.name})'


class EventProductStock(BaseModel):
    event_product = models.OneToOneField(
        EventProduct,
        on_delete=models.CASCADE,
        related_name='stock',
    )
    quantity = models.IntegerField(null=True, blank=True, help_text='Null = stock ilimitado')

    def __str__(self):
        if self.quantity is None:
            return f'{self.event_product.display_name}: ilimitado'
        return f'{self.event_product.display_name}: {self.quantity}'


class EventProductStockRecord(BaseModel):
    class Reason(models.TextChoices):
        INITIAL = 'INITIAL', 'Stock inicial'
        ADMIN_ADJUST = 'ADMIN_ADJUST', 'Ajuste admin'
        SALE = 'SALE', 'Venta'
        SALE_CANCEL = 'SALE_CANCEL', 'Cancelación de venta'
        MIGRATION = 'MIGRATION', 'Migración'
        ORDER_MINT = 'ORDER_MINT', 'Emisión online'

    event_product = models.ForeignKey(
        EventProduct,
        on_delete=models.CASCADE,
        related_name='stock_records',
    )
    delta = models.IntegerField()
    reason = models.CharField(max_length=20, choices=Reason.choices)
    balance_after = models.IntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='stock_adjustments',
    )
    caja_sale = models.ForeignKey(
        'CajaSale',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='stock_records',
    )
    order = models.ForeignKey(
        Order,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='stock_records',
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.event_product.display_name} {self.delta:+d} ({self.reason})'


class EventCaja(BaseModel):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='cajas')
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    products = models.ManyToManyField(
        EventProduct,
        through='EventCajaProduct',
        related_name='cajas',
    )

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name = 'Caja'
        verbose_name_plural = 'Cajas'

    def __str__(self):
        return f'{self.name} ({self.event.name})'


class EventCajaProduct(BaseModel):
    event_caja = models.ForeignKey(EventCaja, on_delete=models.CASCADE)
    event_product = models.ForeignKey(EventProduct, on_delete=models.CASCADE)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']
        unique_together = [['event_caja', 'event_product']]

    def __str__(self):
        return f'{self.event_caja.name} - {self.event_product.display_name}'


class EventCajaMercadoPagoConfig(BaseModel):
    event_caja = models.OneToOneField(
        EventCaja,
        on_delete=models.CASCADE,
        related_name='mercadopago_config',
    )
    external_store_id = models.CharField(max_length=64, blank=True)
    external_pos_id = models.CharField(max_length=64, blank=True)
    store_id = models.BigIntegerField(null=True, blank=True)
    pos_id = models.BigIntegerField(null=True, blank=True)
    terminal_id = models.CharField(max_length=128, blank=True)

    def __str__(self):
        return f'MP config {self.event_caja.name}'

    @property
    def qr_ready(self):
        return bool(self.external_pos_id and self.store_id and self.pos_id)

    @property
    def point_ready(self):
        return bool(self.terminal_id)


class CajaSale(BaseModel):
    class PaymentMethod(models.TextChoices):
        EFECTIVO = 'EFECTIVO', 'Efectivo'
        TRANSFERENCIA = 'TRANSFERENCIA', 'Transferencia'
        MP_QR = 'MP_QR', 'Mercado Pago QR'
        MP_POINT = 'MP_POINT', 'Mercado Pago Postnet'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pendiente'
        PAID = 'PAID', 'Pagada'
        CANCELLED = 'CANCELLED', 'Cancelada'
        EXPIRED = 'EXPIRED', 'Expirada'

    event_caja = models.ForeignKey(EventCaja, on_delete=models.CASCADE, related_name='sales')
    sold_by = models.ForeignKey(User, on_delete=models.RESTRICT, related_name='caja_sales')
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    total_amount = models.DecimalField(decimal_places=2, max_digits=10, default=Decimal('0'))
    customer_email = models.EmailField(blank=True)
    mark_as_used = models.BooleanField(default=False)
    order = models.ForeignKey(
        Order,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='caja_sales',
    )
    mp_order_id = models.CharField(max_length=64, blank=True)
    mp_payment_id = models.CharField(max_length=64, blank=True)
    mp_qr_data = models.TextField(blank=True)
    processor_callback = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Venta {self.id} - {self.event_caja.name} ({self.status})'


class CajaSaleLine(BaseModel):
    caja_sale = models.ForeignKey(CajaSale, on_delete=models.CASCADE, related_name='lines')
    event_product = models.ForeignKey(EventProduct, on_delete=models.RESTRICT)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(decimal_places=2, max_digits=10)

    class Meta:
        ordering = ['id']

    @property
    def line_total(self):
        return self.unit_price * self.quantity

    def __str__(self):
        return f'{self.quantity}x {self.event_product.display_name}'
