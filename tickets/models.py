import uuid
import qrcode
from io import BytesIO
from PIL import Image

from django.db import models
from django.conf import settings
from django.urls import reverse

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

    def __str__(self):
        return self.name


class Order(BaseModel):
    key = models.UUIDField(default=uuid.uuid4, editable=False)
    first_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=255)
    email = models.CharField(max_length=320)
    phone = models.CharField(max_length=50)
    dni = models.CharField(max_length=10)
    donations = models.JSONField(null=True, blank=True)
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

    def get_payment_preference(self):

        import mercadopago
        sdk = mercadopago.SDK(settings.MERCADOPAGO['ACCESS_TOKEN'])

        preference_data = {
            "items": [{
                    "title": self.ticket_type.name,
                    "quantity": 1,
                    "unit_price": ticket.price,
                } for ticket in self.ticket_set.all()],
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

        print(preference_data)

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

    def send_email(self):

        url = reverse('ticket_detail', kwargs={'ticket_key': self.key})

        print(f'{settings.APP_URL}{url}')

        img = qrcode.make(f'{settings.APP_URL}{url}')

        # logo = Image.open('tickets/static/img/logo.png')
        # img.paste(logo)

        stream = BytesIO()
        img.save(stream, format="png")
        stream.seek(0)
        imgObj = stream.read()

        inline_img = InlineImage(content=imgObj, filename='qr.png', subtype='png')

        return send_mail(
            template_name='ticket',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[settings.DEFAULT_FROM_EMAIL, self.email],
            context={
                'ticket': self,
                'qr': inline_img
            }
        )


