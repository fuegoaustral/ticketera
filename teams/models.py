from auditlog.registry import auditlog
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from utils.models import BaseModel


class Team(BaseModel):
    """Equipo global de Fuego Austral (ESTAFA, etc.). No depende de un evento."""
    name = models.CharField('Nombre', max_length=100, unique=True)
    slug = models.SlugField(max_length=50, unique=True)
    description = models.TextField('Descripción', blank=True)

    class Meta:
        verbose_name = 'Equipo'
        verbose_name_plural = 'Equipos'
        ordering = ['name']

    def __str__(self):
        return self.name

    def is_member(self, user, on=None):
        return self.memberships.active(on).filter(user=user).exists()


class TeamMembershipQuerySet(models.QuerySet):
    def active(self, on=None):
        on = on or timezone.localdate()
        return self.filter(Q(ended_on__isnull=True) | Q(ended_on__gte=on), started_on__lte=on)


class TeamMembership(BaseModel):
    """Un período de pertenencia a un equipo. Quien sale y vuelve tiene un período nuevo."""
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='memberships', verbose_name='Equipo')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='team_memberships', verbose_name='Persona')
    started_on = models.DateField('Desde')
    ended_on = models.DateField('Hasta', null=True, blank=True, help_text='Último día en el equipo. Vacío si sigue activa.')
    notes = models.TextField('Notas', blank=True)

    objects = TeamMembershipQuerySet.as_manager()

    class Meta:
        verbose_name = 'Membresía'
        verbose_name_plural = 'Membresías'
        ordering = ['-started_on']
        constraints = [
            models.CheckConstraint(
                check=Q(ended_on__isnull=True) | Q(ended_on__gte=models.F('started_on')),
                name='team_membership_ends_after_start',
            ),
            models.UniqueConstraint(
                fields=['team', 'user'], condition=Q(ended_on__isnull=True),
                name='team_membership_one_open_period',
            ),
        ]

    def __str__(self):
        return f'{self.user} en {self.team}'

    @property
    def is_active(self):
        today = timezone.localdate()
        return self.started_on <= today and (self.ended_on is None or self.ended_on >= today)

    def clean(self):
        if self.ended_on and self.started_on and self.ended_on < self.started_on:
            raise ValidationError({'ended_on': 'La fecha de salida no puede ser anterior a la de ingreso.'})
        if not (self.team_id and self.user_id and self.started_on):
            return
        overlapping = TeamMembership.objects.filter(team_id=self.team_id, user_id=self.user_id).exclude(pk=self.pk).filter(
            Q(ended_on__isnull=True) | Q(ended_on__gte=self.started_on),
        )
        if self.ended_on:
            overlapping = overlapping.filter(started_on__lte=self.ended_on)
        if overlapping.exists():
            raise ValidationError('Esta persona ya tiene un período en el equipo que se superpone con estas fechas.')


auditlog.register(Team)
auditlog.register(TeamMembership)
