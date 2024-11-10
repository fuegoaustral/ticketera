from django.db import models
from django.contrib.auth.models import User
from django.core.validators import RegexValidator

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

    def __str__(self):
        return self.user.username
