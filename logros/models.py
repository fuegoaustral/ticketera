from django.conf import settings
from django.db import models

from utils.models import BaseModel


def normalize_redeem_code(value):
    """Normaliza códigos de canje: trim + uppercase. Vacío → None."""
    if value is None:
        return None
    normalized = str(value).strip().upper()
    return normalized or None


class Achievement(BaseModel):
    """Definición de un logro desbloqueable."""

    class ConditionType(models.TextChoices):
        PURCHASED_EVENTS = 'purchased_events', 'Compró en eventos'
        VOLUNTEER_AT_EVENTS = 'volunteer_at_events', 'Voluntario en eventos'
        ATTENDED_EVENTS = 'attended_events', 'Asistió a eventos'

    slug = models.SlugField(unique=True, max_length=64)
    name = models.CharField(max_length=255)
    image = models.ImageField(
        upload_to='logros/',
        help_text='Imagen del logro (se sube a S3)',
    )
    description = models.TextField(blank=True)
    condition_type = models.CharField(
        max_length=32,
        choices=ConditionType.choices,
        blank=True,
        null=True,
        help_text='Vacío = solo canje por código o asignación manual (sin auto-unlock).',
    )
    condition_config = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            'purchased_events: {"event_ids": [9, 10, 17]} (AND). '
            'volunteer_at_events: {"role": "transmutator", "must_be_used": true} '
            '(cualquier evento; opcional event_ids para limitar; '
            'role: transmutator | ranger | caos | mad). '
            'attended_events: {"event_ids": [14, 7, 4, 1], "min_count": 2} '
            '(al menos N eventos distintos; must_be_used default true).'
        ),
    )
    redeem_code = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        unique=True,
        help_text=(
            'Código secreto compartido para canjear esta figurita en Figuritas. '
            'Se normaliza a mayúsculas. Vacío = no canjeable por código.'
        ),
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name = 'Logro'
        verbose_name_plural = 'Logros'
        permissions = [
            ('manage_achievements', 'Can manage achievements'),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.redeem_code = normalize_redeem_code(self.redeem_code)
        if not self.condition_type:
            self.condition_type = None
        super().save(*args, **kwargs)

    @property
    def image_url(self):
        return self.image.url if self.image else ''

    @property
    def is_redeemable(self):
        return bool(self.redeem_code)


class UserAchievement(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='achievements',
    )
    achievement = models.ForeignKey(
        Achievement,
        on_delete=models.CASCADE,
        related_name='user_achievements',
    )
    unlocked_at = models.DateTimeField(auto_now_add=True)
    celebration_shown = models.BooleanField(
        default=False,
        help_text='True cuando el usuario ya vio el modal de desbloqueo',
    )
    revoked = models.BooleanField(
        default=False,
        help_text='Si True, el logro no cuenta como desbloqueado y el auto-unlock no lo re-otorga.',
    )
    granted_manually = models.BooleanField(
        default=False,
        help_text='True cuando un admin lo asignó (o re-asignó) a mano.',
    )

    class Meta:
        unique_together = [('user', 'achievement')]
        verbose_name = 'Logro de usuario'
        verbose_name_plural = 'Logros de usuarios'

    def __str__(self):
        return f'{self.user} — {self.achievement}'
