from django.db import models


class BaseModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Coupon(BaseModel):
    token = models.CharField(max_length=20)
    max_tickets = models.IntegerField()

    def __str__(self):
        return self.token


class TicketType(BaseModel):
    price = models.DecimalField(decimal_places=2, max_digits=10, null=True, blank=True)
    price_with_coupon = models.DecimalField(decimal_places=2, max_digits=10, null=True, blank=True)
    date_from = models.DateTimeField(null=True, blank=True)
    date_to = models.DateTimeField(null=True, blank=True)
    coupon = models.ForeignKey('Coupon', null=True, blank=True, on_delete=models.RESTRICT)
    name = models.CharField(max_length=100)
    description = models.TextField(max_length=2000, blank=True)
    color = models.CharField(max_length=6, default='6633ff')
    emoji=models.CharField(max_length=1, default='🖕')
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


class Order(BaseModel):
    first_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=255)
    email = models.CharField(max_length=320)
    phone = models.CharField(max_length=50)
    dni = models.CharField(max_length=10)
    donations = models.JSONField()
    amount = models.DecimalField(decimal_places=2, max_digits=10)

    coupon = models.ForeignKey('Coupon', null=True, blank=True, on_delete=models.RESTRICT)
    ticket_type = models.ForeignKey('TicketType', on_delete=models.RESTRICT)

    class OrderStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pendiente'
        PAID = 'PAID', 'Pagada'
        ERROR = 'ERROR', 'Error'
    status = models.CharField(
        max_length=20,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING
    )


class Ticket(BaseModel):
    first_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=255)
    email = models.CharField(max_length=320)
    phone = models.CharField(max_length=50)
    dni = models.CharField(max_length=10)