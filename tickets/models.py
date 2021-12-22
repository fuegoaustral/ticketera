from datetime import datetime
import uuid
import qrcode
from io import BytesIO

from django.db import models
from django.db.models import Count, Sum, Q, F
from django.conf import settings
from django.urls import reverse

from auditlog.registry import auditlog

from deprepagos.email import send_mail
from templated_email import InlineImage


class BaseModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


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
    def get_cheapeast_available(self, coupon):
        ticket_type = (TicketType.objects
            # filter by date
            .filter(Q(date_from__lte=datetime.now()) | Q(date_from__isnull=True))
            .filter(Q(date_to__gte=datetime.now()) | Q(date_to__isnull=True))

            # filter by sold out tickets
            .annotate(confirmed_tickets=Count('order__ticket', filter=Q(order__status=Order.OrderStatus.CONFIRMED)))
            .annotate(available_tickets=F('ticket_count') - F('confirmed_tickets'))
            .filter(available_tickets__gt=0)
        )

        # add coupon filter
        if coupon:
            ticket_type = ticket_type.filter(coupon=coupon)
        else:
            # filter the ones only for coupons
            ticket_type = ticket_type.filter(price__isnull=False, price__gt=0)

        ticket_type = (ticket_type
            .order_by('-price_with_coupon' if coupon else '-price')
            .first()
        )

        # block purchase if the allowed tickets for a coupon have been sold
        if coupon and coupon.tickets_remaining() <= 0:
            ticket_type = None

        return ticket_type


class TicketType(BaseModel):
    price = models.DecimalField(decimal_places=2, max_digits=10, null=True, blank=True)
    price_with_coupon = models.DecimalField(decimal_places=2, max_digits=10, null=True, blank=True)
    date_from = models.DateTimeField(null=True, blank=True)
    date_to = models.DateTimeField(null=True, blank=True)
    name = models.CharField(max_length=100)
    description = models.TextField(max_length=2000, blank=True)
    color = models.CharField(max_length=6, default='6633ff')
    emoji=models.CharField(max_length=20, default='🖕')
    ticket_count=models.IntegerField()

    objects = TicketTypeManager()

    # class Meta:
    #     constraints = [
    #         models.CheckConstraint(
    #             name="%(app_label)s_%(class)s_price_or_price_with_coupon",
    #             check=(
    #                 models.Q(price__isnull=True, price_with_coupon__isnull=False)
    #                 | models.Q(price__isnull=False, price_with_coupon__isnull=True)
    #                 | models.Q(price__isnull=True, price_with_coupon__isnull=True)
    #             ),
    #         )
    #     ]

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
        return self.name


class Order(BaseModel):
    key = models.UUIDField(default=uuid.uuid4, editable=False)
    first_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=255)
    email = models.CharField(max_length=320)
    phone = models.CharField(max_length=50)
    dni = models.CharField(max_length=10)
    donation_art = models.DecimalField('Becas de Arte', decimal_places=2, max_digits=10, blank=True, null=True, help_text='Para empujar la creatividad en nuestra ciudad temporal.')
    donation_venue = models.DecimalField('Donaciones a La Sede', decimal_places=2, max_digits=10, blank=True, null=True, help_text='Para ayudar a quienes necesitan una mano con su bono contribución.')
    donation_grant = models.DecimalField('Becas No Tengo Un Mango (NTUM)', decimal_places=2, max_digits=10, blank=True, null=True, help_text='Para mejorar el espacio donde nos encontramos todo el año.')
    amount = models.DecimalField(decimal_places=2, max_digits=10)

    coupon = models.ForeignKey('Coupon', null=True, blank=True, on_delete=models.RESTRICT)
    ticket_type = models.ForeignKey('TicketType', on_delete=models.RESTRICT)

    response = models.JSONField(null=True, blank=True)

    class OrderStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pendiente'
        CONFIRMED = 'CONFIRMED', 'Confirmada'
        ERROR = 'ERROR', 'Error'
    status = models.CharField(
        max_length=20,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING
    )

    def get_resource_url(self):
        return reverse('order_detail', kwargs={'order_key': self.key})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._old_status = self.status

    def save(self):
        super(Order, self).save()
        if self._old_status != Order.OrderStatus.CONFIRMED and self.status == Order.OrderStatus.CONFIRMED:
            self.send_confirmation_email()
            for ticket in self.ticket_set.all():
                ticket.send_email()

    def send_confirmation_email(self):
        send_mail(
            template_name='order_success',
            recipient_list=[self.email],
            context={
                'order': self,
                'url': self.get_resource_url()
            }
        )

    def get_payment_preference(self):

        import mercadopago
        sdk = mercadopago.SDK(settings.MERCADOPAGO['ACCESS_TOKEN'])

        items = []
        items.extend([{
                    "title": self.ticket_type.name,
                    "quantity": 1,
                    "unit_price": ticket.price,
                } for ticket in self.ticket_set.all() if ticket.price >0])

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
                "identification": { "type": "DNI", "number": self.dni },
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

        print('PREFERENCE', preference_data)
        response = sdk.preference().create(preference_data)['response']
        print('RESPONSE', response)
        return response

    def __str__(self):
        return f'#{self.pk} {self.last_name}'


class Ticket(BaseModel):
    key = models.UUIDField(default=uuid.uuid4, editable=False)
    first_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=255)
    email = models.CharField(max_length=320)
    phone = models.CharField(max_length=50)
    dni = models.CharField(max_length=10)

    price = models.IntegerField(default=0)
    order = models.ForeignKey('Order', on_delete=models.CASCADE)

    # volunteer
    VOLUNTEER_CHOICES = (
        ('ask', 'No sé aún'),
        ('no', 'No me interesa'),
        ('yes', 'Quiero ser parte del voluntariado'),
    )
    volunteer = models.CharField(choices=VOLUNTEER_CHOICES, max_length=10)
    volunteer_ranger = models.BooleanField('Rangers')
    volunteer_transmutator = models.BooleanField('Transmutadores')
    volunteer_umpalumpa = models.BooleanField('Umpa Lumpas (Armado y Desarme de la Ciudad)')

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
                # 'qr': inline_img
            }
        )



auditlog.register(Coupon)
auditlog.register(TicketType)
auditlog.register(Order)
auditlog.register(Ticket)
