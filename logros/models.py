from django.conf import settings
from django.db import models

from utils.models import BaseModel


class Achievement(BaseModel):
    """Definición de un logro desbloqueable."""

    class ConditionType(models.TextChoices):
        PURCHASED_EVENTS = 'purchased_events', 'Compró en eventos'

    slug = models.SlugField(unique=True, max_length=64)
    name = models.CharField(max_length=255)
    image = models.CharField(
        max_length=255,
        help_text='Ruta relativa a MEDIA_ROOT, ej: logros/3-oscuras.jpg',
    )
    description = models.TextField(blank=True)
    condition_type = models.CharField(max_length=32, choices=ConditionType.choices)
    condition_config = models.JSONField(
        default=dict,
        blank=True,
        help_text='Parámetros de la condición, ej: {"event_ids": [9, 10, 17]}',
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name = 'Logro'
        verbose_name_plural = 'Logros'

    def __str__(self):
        return self.name

    @property
    def image_url(self):
        return f'{settings.MEDIA_URL}{self.image}'


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

    class Meta:
        unique_together = [('user', 'achievement')]
        verbose_name = 'Logro de usuario'
        verbose_name_plural = 'Logros de usuarios'

    def __str__(self):
        return f'{self.user} — {self.achievement}'
