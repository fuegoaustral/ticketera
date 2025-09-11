import base64
import logging
import uuid
from datetime import datetime
from decimal import Decimal
from io import BytesIO

import jsonfield
import qrcode
from auditlog.registry import auditlog
from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Count, Sum, Q, F
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from utils.email import send_mail
from utils.models import BaseModel
from .processing import mint_tickets


class Coupon(BaseModel):
    token = models.CharField(max_length=20)
    max_tickets = models.IntegerField()
    ticket_type = models.ForeignKey('TicketType', on_delete=models.CASCADE)

    def __str__(self):
        return self.token

    def tickets_remaining(self):
        tickets_sold = (Order.objects
                        .filter(coupon=self)
                        .filter(status=Order.OrderStatus.CONFIRMED)
                        .annotate(num_tickets=Count('ticket'))
                        .aggregate(tickets_sold=Sum('num_tickets')
                                   ))['tickets_sold'] or 0

        try:
            return max(0, self.max_tickets - (tickets_sold or 0))
        except TypeError:
            return None


class TicketTypeManager(models.Manager):
    # get all available ticket types for available events
    def get_available_ticket_types_for_current_events(self):
        active_events = Event.get_active_events()
        return (self
                .filter(event__in=active_events)
                .filter(Q(date_from__lte=timezone.now()) | Q(date_from__isnull=True))
                .filter(Q(date_to__gte=timezone.now()) | Q(date_to__isnull=True))
                .filter(Q(ticket_count__gt=0) | Q(ticket_count__isnull=True))
                .filter(is_direct_type=False)
                .order_by('cardinality', 'price')
                )

    def get_next_ticket_type_available(self, event):
        return (self
                .filter(event=event)
                .filter(Q(date_from__gte=timezone.now()) | Q(date_from__isnull=True))
                .filter(Q(ticket_count__gt=0) | Q(ticket_count__isnull=True))
                .order_by('cardinality', 'price')
                .first()
                )

    def get_available(self, coupon, event):
        if event.tickets_remaining() <= 0:
            return TicketType.objects.none()

        # Get the current time using Django's timezone utility
        now = timezone.now()

        # Query available ticket types for the event
        ticket_types = TicketType.objects.filter(event=event).filter(
            Q(date_from__lte=timezone.now()) | Q(date_from__isnull=True),
            Q(date_to__gte=timezone.now()) | Q(date_to__isnull=True),
            Q(ticket_count__gt=0) | Q(ticket_count__isnull=True)
        )

        # Apply coupon filtering if a coupon is provided
        if coupon:
            if coupon.tickets_remaining() <= 0:
                return TicketType.objects.none()

            ticket_types = ticket_types.filter(coupon=coupon)
        else:
            # Exclude ticket types that are only available with coupons
            # Allow tickets with price 0 (free tickets with custom amount)
            ticket_types = ticket_types.filter(price__isnull=False, price__gte=0)

        # If event does not show multiple tickets, return the cheapest available ticket
        if not event.show_multiple_tickets:
            first_ticket = ticket_types.order_by('price_with_coupon' if coupon else 'price').first()
            if first_ticket:
                return ticket_types.filter(id=first_ticket.id)
            else:
                return TicketType.objects.none()

        return ticket_types


class TicketType(BaseModel):
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    price = models.DecimalField(decimal_places=2, max_digits=10, null=True, blank=True)
    price_with_coupon = models.DecimalField(decimal_places=2, max_digits=10, null=True, blank=True)
    date_from = models.DateTimeField(null=True, blank=True)
    date_to = models.DateTimeField(null=True, blank=True)
    name = models.CharField(max_length=100)
    description = models.TextField(max_length=2000, blank=True)
    color = models.CharField(max_length=6, default='6633ff')
    emoji = models.CharField(max_length=255, default='🖕')
    ticket_count = models.IntegerField()
    cardinality = models.IntegerField(null=True, blank=True, help_text="Optional ordering number for ticket types")

    objects = TicketTypeManager()

    is_direct_type = models.BooleanField(default=False)

    def get_corresponding_ticket_type(coupon: Coupon):
        return TicketType.objects \
            .annotate(confirmed_tickets=Count('order__ticket', filter=Q(order__status=Order.OrderStatus.CONFIRMED))) \
            .annotate(available_tickets=F('ticket_count') - F('confirmed_tickets')) \
            .filter(coupon=coupon) \
            .filter(Q(date_from__lte=datetime.now()) | Q(date_from__isnull=True)) \
            .filter(Q(date_to__gte=datetime.now()) | Q(date_to__isnull=True)) \
            .order_by('price' if coupon is None else '-price_with_coupon') \
            .first()

    def __str__(self):
        return f"{self.name} ({self.event.name})"


class OrderTicket(BaseModel):
    order = models.ForeignKey('Order', related_name='order_tickets', on_delete=models.CASCADE)
    ticket_type = models.ForeignKey('TicketType', related_name='order_tickets', on_delete=models.RESTRICT)
    quantity = models.PositiveIntegerField(default=1)


class Order(BaseModel):
    class OrderStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pendiente'
        PROCESSING = 'PROCESSING', 'Procesando'
        CONFIRMED = 'CONFIRMED', 'Confirmada'
        ERROR = 'ERROR', 'Error'
        REFUNDED = 'REFUNDED', 'Reembolsada'

    class OrderType(models.TextChoices):
        INTERNATIONAL_TRANSFER = 'INTERNATIONAL_TRANSFER', 'Transferencia Internacional'
        LOCAL_TRANSFER = 'LOCAL_TRANSFER', 'Transferencia Local'
        ONLINE_PURCHASE = 'ONLINE_PURCHASE', 'Compra Online'
        CASH_ONSITE = 'CASH_ONSITE', 'Efectivo'
        OTHER = 'OTHER', 'Otro'

    key = models.UUIDField(default=uuid.uuid4, editable=False)

    first_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=255)
    email = models.CharField(max_length=320)
    phone = models.CharField(max_length=50)
    dni = models.CharField(max_length=10)
    donation_art = models.DecimalField('Becas de Arte $', validators=[MinValueValidator(Decimal('0'))],
                                       decimal_places=0, max_digits=10, blank=True, null=True,
                                       help_text='Para empujar la creatividad en nuestra ciudad temporal.')
    donation_venue = models.DecimalField('Donaciones a La Sede $', validators=[MinValueValidator(Decimal('0'))],
                                         decimal_places=0, max_digits=10, blank=True, null=True,
                                         help_text='Para mejorar el espacio donde nos encontramos todo el año.')
    donation_grant = models.DecimalField('Beca Inclusión Radical $', validators=[MinValueValidator(Decimal('0'))],
                                         decimal_places=0, max_digits=10, blank=True, null=True,
                                         help_text='Para ayudar a quienes necesitan una mano con su bono contribución.')
    amount = models.DecimalField(decimal_places=2, max_digits=10)

    coupon = models.ForeignKey('Coupon', null=True, blank=True, on_delete=models.RESTRICT)

    response = models.JSONField(null=True, blank=True)
    event = models.ForeignKey(Event, null=True, blank=True, on_delete=models.RESTRICT, related_name='orders')
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.RESTRICT, related_name='orders')

    status = models.CharField(
        max_length=20,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING
    )

    order_type = models.CharField(
        max_length=32,
        choices=OrderType.choices,
        default=OrderType.ONLINE_PURCHASE
    )

    notes = models.TextField(null=True, blank=True)
    generated_by_admin_user = models.ForeignKey(
        User,
        related_name="generated_orders",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        help_text="Usuario admin que generó la orden",
    )

    class Meta:
        permissions = [
            ("can_sell_tickets", "Can sell tickets in Caja"),
        ]

    def total_ticket_types(self):
        return self.order_tickets.count()

    def total_order_tickets(self):
        return self.order_tickets.aggregate(total=Sum('quantity'))['total']

    def get_resource_url(self):
        return reverse('order_detail', kwargs={'order_key': self.key})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._old_status = self.status

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.status == Order.OrderStatus.PROCESSING and self._old_status != self.status:
            self._old_status = self.status
            mint_tickets(self)

    def send_confirmation_email(self):

        send_mail(
            template_name='order_success',
            recipient_list=[self.email],
            context={
                'order': self,
                'event': self.event,
                'has_many_tickets': NewTicket.objects.filter(holder=self.user, event=self.event).count() > 1,
            }
        )
        logging.info(f'Order {self.id} confirmation email sent')

    def get_payment_preference(self):

        import mercadopago
        sdk = mercadopago.SDK(settings.MERCADOPAGO['ACCESS_TOKEN'])

        items = []
        items.extend([{
            "title": self.ticket_type.name,
            "quantity": 1,
            "unit_price": ticket.price,
        } for ticket in self.ticket_set.all() if ticket.price >= 0])

        if self.donation_art:
            items.append({
                "title": 'Contribución Becas de Arte',
                "quantity": 1,
                "unit_price": float(self.donation_art),
            })

        if self.donation_venue:
            items.append({
                "title": 'Contribución a La Sede',
                "quantity": 1,
                "unit_price": float(self.donation_venue),
            })

        if self.donation_grant:
            items.append({
                "title": 'Contribución a Becas No Tengo Un Mango',
                "quantity": 1,
                "unit_price": float(self.donation_grant),
            })

        preference_data = {
            "items": items,
            "payer": {
                "name": self.first_name,
                "surname": self.last_name,
                "email": self.email,
                "identification": {"type": "DNI", "number": self.dni},
            },
            "back_urls": {
                "success": settings.APP_URL + reverse("payment_success_callback", kwargs={'order_key': self.key}),
                "failure": settings.APP_URL + reverse("payment_failure_callback", kwargs={'order_key': self.key}),
                "pending": settings.APP_URL + reverse("payment_pending_callback", kwargs={'order_key': self.key})
            },
            "auto_return": "approved",
            # IPN makes the thing go faulty. Is it worthy to investigate?
            # "notification_url": settings.APP_URL + reverse("payment_notification"),
            "statement_descriptor": "Fuego Austral 2022",
            "external_reference": self.id,
        }

        response = sdk.preference().create(preference_data)['response']
        return response

    def __str__(self):
        return f'Order #{self.pk}  {self.last_name} - {self.email} - {self.status} - {self.amount}'


class NewTicket(BaseModel):
    key = models.UUIDField(default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    order = models.ForeignKey('Order', on_delete=models.CASCADE, related_name='new_tickets')
    ticket_type = models.ForeignKey('TicketType', on_delete=models.CASCADE)
    owner = models.ForeignKey(User, related_name='owned_tickets', null=True, blank=True, on_delete=models.SET_NULL)
    holder = models.ForeignKey(User, related_name='held_tickets', null=True, blank=True, on_delete=models.CASCADE)
    is_used = models.BooleanField(default=False)

    volunteer_ranger = models.BooleanField('Rangers', null=True, blank=True, )
    volunteer_transmutator = models.BooleanField('Transmutadores', null=True, blank=True, )
    volunteer_umpalumpa = models.BooleanField('CAOS (Desarme de la Ciudad)', null=True, blank=True, )

    def generate_qr_code(self):
        # Generate the QR code
        img = qrcode.make(f'{self.key}')
        img_io = BytesIO()
        img.save(img_io, format='PNG')
        img_io.seek(0)

        # Encode the image to base64
        img_data_base64 = base64.b64encode(img_io.getvalue()).decode('utf-8')

        return img_data_base64

    def save(self):
        with transaction.atomic():
            is_new = self.pk is None
            super(NewTicket, self).save()

            if is_new:
                ticket_type = TicketType.objects.get(id=self.ticket_type.id)
                ticket_type.ticket_count = ticket_type.ticket_count - 1
                ticket_type.save()

    def get_dto(self, user):
        transfer_pending = NewTicketTransfer.objects.filter(ticket=self, tx_from=user,
                                                            status='PENDING').first()
        return {
            'key': self.key,
            'order': self.order.key,
            'ticket_type': self.ticket_type.name,
            'ticket_color': self.ticket_type.color,
            'emoji': self.ticket_type.emoji,
            'price': self.ticket_type.price,
            'is_transfer_pending': transfer_pending is not None,
            'transferring_to': transfer_pending.tx_to_email if transfer_pending else None,
            'is_owners': self.holder == self.owner,
            'volunteer_ranger': self.volunteer_ranger,
            'volunteer_transmutator': self.volunteer_transmutator,
            'volunteer_umpalumpa': self.volunteer_umpalumpa,
            'qr_code': self.generate_qr_code(),
            'is_used': self.is_used,
        }

    def is_volunteer(self):
        return self.volunteer_ranger or self.volunteer_transmutator or self.volunteer_umpalumpa

    def __str__(self):
        return f'Ticket {self.key} - {self.ticket_type} - holder: {self.holder} - owner: {self.owner}'

    class Meta:
        verbose_name = 'Ticket'


class NewTicketTransfer(BaseModel):
    ticket = models.ForeignKey(NewTicket, on_delete=models.CASCADE)
    tx_from = models.ForeignKey(User, related_name='transferred_tickets', null=True, blank=True,
                                on_delete=models.CASCADE)
    tx_to = models.ForeignKey(User, related_name='received_tickets', null=True, blank=True, on_delete=models.CASCADE)
    tx_to_email = models.CharField(max_length=320)
    TRANSFER_STATUS = (('PENDING', 'Pendiente'), ('CONFIRMED', 'Confirmado'), ('CANCELLED', 'Cancelado'))
    status = models.CharField(max_length=10, choices=TRANSFER_STATUS, default='PENDING')

    def __str__(self):
        return f'Transaction from {self.tx_from.email} to {self.tx_to_email}  - Ticket  {self.ticket.key} - {self.status} - Ceated {(timezone.now() - self.created_at).days} days ago'

    class Meta:
        verbose_name = 'Ticket transfer'


class TicketPerson(BaseModel):
    first_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=255)
    email = models.CharField(max_length=320)
    phone = models.CharField(max_length=50)
    dni = models.CharField(max_length=10)

    # volunteer
    VOLUNTEER_CHOICES = (
        ('ask', 'No sé aún'),
        ('no', 'No me interesa'),
        ('yes', 'Quiero ser parte del voluntariado'),
    )
    volunteer = models.CharField(choices=VOLUNTEER_CHOICES, max_length=10)
    volunteer_ranger = models.BooleanField('Rangers')
    volunteer_transmutator = models.BooleanField('Transmutadores')
    volunteer_umpalumpa = models.BooleanField('CAOS (Desarme de la Ciudad)')

    class Meta:
        abstract = True


class Ticket(TicketPerson, BaseModel):
    key = models.UUIDField(default=uuid.uuid4, editable=False)

    price = models.IntegerField(default=0)
    order = models.ForeignKey('Order', on_delete=models.CASCADE)

    def __str__(self):
        return f'({self.order.status}) {self.first_name} {self.last_name}'

    def get_absolute_url(self):
        return reverse('ticket_detail', args=(self.key,))

    def send_email(self):
        # img = qrcode.make(f'{settings.APP_URL}{url}')

        # logo = Image.open('tickets/static/img/logo.png')
        # img.paste(logo)

        # stream = BytesIO()
        # img.save(stream, format="png")
        # stream.seek(0)
        # imgObj = stream.read()

        # inline_img = InlineImage(content=imgObj, filename='qr.png', subtype='png')

        return send_mail(
            template_name='ticket',
            recipient_list=[self.email],
            context={
                'ticket': self,
                'event': self.order.ticket_type.event,
                # 'qr': inline_img
            }
        )


class TicketTransfer(TicketPerson, BaseModel):
    key = models.UUIDField(default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE)
    transferred = models.BooleanField(default=False)

    def __str__(self):
        return f'Transferencia a {self.first_name} {self.last_name}'

    def transfer(self):
        self.transferred = True
        self.ticket.first_name = self.first_name
        self.ticket.last_name = self.last_name
        self.ticket.email = self.email
        self.ticket.phone = self.phone
        self.ticket.dni = self.dni
        self.ticket.save()
        self.ticket.send_email()
        self.save()

    def send_email(self):
        return send_mail(
            template_name='transfer',
            recipient_list=[self.ticket.email],
            context={
                'transfer': self,
                'ticket': self.ticket,
            }
        )

    def get_absolute_url(self):
        return reverse('ticket_transfer_confirmed', args=(self.key,))


class MessageIdempotency(BaseModel):
    email = models.EmailField()
    hash = models.CharField(max_length=64, unique=True)
    payload = jsonfield.JSONField()

    def __str__(self):
        return f"{self.email} - {self.hash}"


class DirectTicketTemplateOriginChoices(models.TextChoices):
    CAMP = 'CAMP', 'Camp'
    VOLUNTEER = 'VOLUNTARIOS', 'Voluntarios'
    ART = 'ARTE', 'Arte'


class DirectTicketTemplateStatus(models.TextChoices):
    AVAILABLE = 'AVAILABLE', 'Disponible'
    PENDING = 'PENDING', 'Pendiente'
    ASSIGNED = 'ASSIGNED', 'Asignados'


class DirectTicketTemplate(BaseModel):
    origin = models.CharField(
        max_length=20,
        choices=DirectTicketTemplateOriginChoices.choices,
        default=DirectTicketTemplateOriginChoices.CAMP,
    )
    name = models.CharField(max_length=255, help_text="Descripción y/o referencias")
    amount = models.PositiveIntegerField()
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=DirectTicketTemplateStatus.choices,
                              default=DirectTicketTemplateStatus.AVAILABLE)
    amount_used = models.PositiveIntegerField(null=True, blank=True)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, null=True, blank=True)


    class Meta:
        verbose_name = "Bono dirigido"
        verbose_name_plural = "Config Bonos dirigidos"
        permissions = [
            ("admin_volunteers", "Can admin Volunteers"),
        ]

    def __str__(self):
        return f"{self.name} ({self.origin}) - {self.amount}"


auditlog.register(Coupon)
auditlog.register(TicketType)
auditlog.register(Order)
auditlog.register(Ticket)
auditlog.register(NewTicket)
auditlog.register(NewTicketTransfer)
auditlog.register(TicketTransfer)
auditlog.register(DirectTicketTemplate)
