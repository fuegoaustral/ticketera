from django.utils import timezone

from logros.conditions import is_condition_met
from logros.models import Achievement, UserAchievement, normalize_redeem_code


class RedeemCodeError(Exception):
    """Error de canje de código de logro."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def serialize_achievement(achievement):
    return {
        'slug': achievement.slug,
        'name': achievement.name,
        'description': achievement.description,
        'image_url': achievement.image_url,
    }


def get_achievements_for_user(user):
    """Lista de logros con estado desbloqueado para la UI."""
    unlocked_by_id = {
        ua.achievement_id: ua
        for ua in UserAchievement.objects.filter(user=user, revoked=False)
    }
    result = []
    for achievement in Achievement.objects.filter(is_active=True):
        user_achievement = unlocked_by_id.get(achievement.id)
        result.append({
            'achievement': achievement,
            'unlocked': user_achievement is not None,
            'unlocked_at': user_achievement.unlocked_at if user_achievement else None,
        })
    return result


def check_and_unlock_for_user(user):
    """
    Evalúa condiciones y persiste logros nuevos.
    Retorna los Achievement recién desbloqueados.

    Si ya existe un UserAchievement (aunque esté revoked), no re-otorga.
    """
    if not user or not user.is_authenticated:
        return []

    already_unlocked = set(
        UserAchievement.objects.filter(user=user).values_list('achievement_id', flat=True)
    )
    newly_unlocked = []

    for achievement in Achievement.objects.filter(is_active=True):
        if achievement.id in already_unlocked:
            continue
        if not achievement.condition_type:
            continue
        if is_condition_met(user, achievement):
            UserAchievement.objects.create(user=user, achievement=achievement)
            newly_unlocked.append(achievement)

    return newly_unlocked


def grant_achievement(user, achievement, *, manual=True):
    """
    Asigna un logro a un usuario (override de query).
    Si estaba revoked, lo reactiva y vuelve a mostrar celebración.
    """
    ua, created = UserAchievement.objects.get_or_create(
        user=user,
        achievement=achievement,
        defaults={
            'granted_manually': manual,
            'revoked': False,
            'celebration_shown': False,
        },
    )
    if not created:
        ua.revoked = False
        ua.granted_manually = manual
        ua.celebration_shown = False
        ua.save(update_fields=[
            'revoked',
            'granted_manually',
            'celebration_shown',
            'updated_at',
        ])
    return ua


def revoke_achievement(user, achievement):
    """
    Quita un logro (override permanente): marca revoked y bloquea auto-regrant.
    Retorna el UserAchievement o None si no existía.
    """
    try:
        ua = UserAchievement.objects.get(user=user, achievement=achievement)
    except UserAchievement.DoesNotExist:
        # Crear fila revoked para bloquear auto-unlock futuro si cumple condición
        return UserAchievement.objects.create(
            user=user,
            achievement=achievement,
            revoked=True,
            granted_manually=True,
            celebration_shown=True,
        )

    if not ua.revoked:
        ua.revoked = True
        ua.save(update_fields=['revoked', 'updated_at'])
    return ua


def redeem_achievement_code(user, code):
    """
    Canjea un código secreto compartido y desbloquea el logro asociado.
    Retorna el Achievement desbloqueado.
    Lanza RedeemCodeError con code in {'empty', 'invalid', 'inactive', 'already_unlocked'}.
    """
    if not user or not user.is_authenticated:
        raise RedeemCodeError('invalid', 'Tenés que iniciar sesión para canjear un código.')

    normalized = normalize_redeem_code(code)
    if not normalized:
        raise RedeemCodeError('empty', 'Ingresá un código.')

    try:
        achievement = Achievement.objects.get(redeem_code=normalized)
    except Achievement.DoesNotExist:
        raise RedeemCodeError('invalid', 'Ese código no es válido.')

    if not achievement.is_active:
        raise RedeemCodeError('inactive', 'Ese logro no está disponible.')

    existing = UserAchievement.objects.filter(user=user, achievement=achievement).first()
    if existing and not existing.revoked:
        raise RedeemCodeError('already_unlocked', 'Ya tenés este logro desbloqueado.')

    grant_achievement(user, achievement, manual=False)
    return achievement


def get_pending_celebrations(user):
    """Logros desbloqueados cuyo modal de celebración aún no se mostró."""
    return list(
        UserAchievement.objects.filter(
            user=user,
            celebration_shown=False,
            revoked=False,
        )
        .select_related('achievement')
        .order_by('unlocked_at')
    )


def pending_celebrations_payload(user):
    return [
        serialize_achievement(ua.achievement)
        for ua in get_pending_celebrations(user)
    ]


def evaluate_and_get_pending_payload(user):
    """Desbloquea logros nuevos y devuelve los que todavía no se celebraron."""
    check_and_unlock_for_user(user)
    return pending_celebrations_payload(user)


def mark_celebrations_shown(user, achievement_slugs=None):
    qs = UserAchievement.objects.filter(
        user=user,
        celebration_shown=False,
        revoked=False,
    )
    if achievement_slugs is not None:
        qs = qs.filter(achievement__slug__in=achievement_slugs)
    qs.update(celebration_shown=True, updated_at=timezone.now())
