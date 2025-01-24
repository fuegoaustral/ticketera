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
                        if user_already_has_ticket:
                            transfer.ticket.owner = None
                        else:
                            transfer.ticket.owner = user
                            user_already_has_ticket = True

                        transfer.ticket.save()