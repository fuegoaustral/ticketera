from django.db import models, transaction
from django.contrib.auth.models import User
from django.core.validators import RegexValidator

from tickets.models import NewTicketTransfer, NewTicket
from utils.models import BaseModel


class Profile(BaseModel):
    DNI = 'DNI'
    PASSPORT = 'PASSPORT'
    OTHER = 'OTHER'

    DOCUMENT_TYPE_CHOICES = [
        (DNI, 'DNI'),
        (PASSPORT, 'Passport'),
        (OTHER, 'Other'),
    ]

    NONE = 'NONE'
    INITIAL_STEP = 'INITIAL_STEP'
    COMPLETE = 'COMPLETE'

    PROFILE_COMPLETION_CHOICES = [
        (NONE, 'None'),
        (INITIAL_STEP, 'Initial Step'),
        (COMPLETE, 'Complete'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    document_type = models.CharField(max_length=10, choices=DOCUMENT_TYPE_CHOICES, default=DNI)
    document_number = models.CharField(max_length=50)
    phone = models.CharField(max_length=15, validators=[RegexValidator(r'^\+?1?\d{9,15}$')])
    profile_completion = models.CharField(max_length=15, choices=PROFILE_COMPLETION_CHOICES, default=NONE)

    def _primary_sede_subscription(self):
        if hasattr(self, '_cached_primary_sede_subscription'):
            return self._cached_primary_sede_subscription
        subs_qs = self.sede_subscriptions.filter(is_soft_removed=False)
        active = subs_qs.filter(is_active=True).order_by('-last_payment_date', '-synced_at').first()
        if active:
            self._cached_primary_sede_subscription = active
            return active
        fallback = subs_qs.order_by('-last_payment_date', '-synced_at').first()
        self._cached_primary_sede_subscription = fallback
        return fallback

    @property
    def miembro_sede(self):
        return self.sede_subscriptions.filter(is_active=True, is_soft_removed=False).exists()

    @property
    def sede_subscription_id(self):
        primary = self._primary_sede_subscription()
        return primary.subscription_id if primary else ''

    @property
    def sede_subscription_status(self):
        primary = self._primary_sede_subscription()
        return primary.status if primary else ''

    @property
    def sede_payment_method(self):
        primary = self._primary_sede_subscription()
        return primary.payment_method if primary else ''

    @property
    def sede_last_payment_date(self):
        primary = self._primary_sede_subscription()
        return primary.last_payment_date if primary else None

    @property
    def sede_last_payment_amount(self):
        primary = self._primary_sede_subscription()
        return primary.last_payment_amount if primary else None

    @property
    def sede_next_payment_date(self):
        primary = self._primary_sede_subscription()
        return primary.next_payment_date if primary else None

    @property
    def sede_member_since(self):
        primary = self._primary_sede_subscription()
        return primary.member_since if primary else None

    @property
    def sede_synced_at(self):
        primary = self._primary_sede_subscription()
        return primary.synced_at if primary else None

    @property
    def sede_payment_method_label(self):
        from user_profile.services.sede_mercadopago import format_payment_method
        return format_payment_method(self.sede_payment_method)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._old_profile_completion = self.profile_completion

    def __str__(self):
        return self.user.username

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.profile_completion == self.COMPLETE and self._old_profile_completion != self.COMPLETE:
            user = self.user
            pending_transfers = (
                NewTicketTransfer.objects.filter(tx_to_email__iexact=user.email, status="PENDING")
                .select_related("ticket")
                .all()
            )

            if pending_transfers.exists():
                with transaction.atomic():
                    user_already_has_ticket = NewTicket.objects.filter(owner=user).exists()
                    for transfer in pending_transfers:
                        transfer.status = "COMPLETED"
                        transfer.tx_to = user
                        transfer.save()

                        transfer.ticket.holder = user
                        transfer.ticket.volunteer_ranger = None
                        transfer.ticket.volunteer_transmutator = None
                        transfer.ticket.volunteer_umpalumpa = None
                        transfer.ticket.volunteer_mad = None
                        if user_already_has_ticket:
                            transfer.ticket.owner = None
                        else:
                            transfer.ticket.owner = user
                            user_already_has_ticket = True

                        transfer.ticket.save()


class SedeSubscription(BaseModel):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='sede_subscriptions')
    subscription_id = models.CharField(max_length=64, unique=True)
    plan_id = models.CharField(max_length=64, blank=True, default='')
    tier_name = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=32, blank=True, default='')
    payment_method = models.CharField(max_length=64, blank=True, default='')
    last_payment_date = models.DateTimeField(null=True, blank=True)
    last_payment_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    next_payment_date = models.DateTimeField(null=True, blank=True)
    member_since = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=False)
    is_soft_removed = models.BooleanField(default=False)
    soft_removed_at = models.DateTimeField(null=True, blank=True)
    matched_via = models.CharField(max_length=16, blank=True, default='')
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-is_active', '-last_payment_date', '-created_at')

    def __str__(self):
        return f'{self.subscription_id} ({self.status})'


class SedeUnmatchedSubscription(BaseModel):
    subscription_id = models.CharField(max_length=64, unique=True)
    plan_id = models.CharField(max_length=64, blank=True, default='')
    tier_name = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=32, blank=True, default='')
    payer_id = models.CharField(max_length=64, blank=True, default='')
    payer_email = models.CharField(max_length=255, blank=True, default='')
    payer_first_name = models.CharField(max_length=255, blank=True, default='')
    payer_last_name = models.CharField(max_length=255, blank=True, default='')
    document_number = models.CharField(max_length=128, blank=True, default='')
    payment_method = models.CharField(max_length=64, blank=True, default='')
    last_payment_date = models.DateTimeField(null=True, blank=True)
    last_payment_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    next_payment_date = models.DateTimeField(null=True, blank=True)
    member_since = models.DateTimeField(null=True, blank=True)
    hints = models.JSONField(default=dict, blank=True)
    unresolved_reason = models.CharField(max_length=255, blank=True, default='')
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-last_seen_at', '-updated_at')

    def __str__(self):
        return f'Unmatched {self.subscription_id} ({self.status})'


class SedeSubscriptionPlan(BaseModel):
    plan_id = models.CharField(max_length=64, unique=True)
    plan_name = models.CharField(max_length=255, blank=True, default='')
    billing_cycle = models.CharField(max_length=64, blank=True, default='')
    is_enabled = models.BooleanField(default=False)
    subscriptions_count = models.PositiveIntegerField(default=0)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-is_enabled', 'plan_name', 'plan_id')

    def __str__(self):
        return f'{self.plan_name or self.plan_id} ({self.plan_id})'