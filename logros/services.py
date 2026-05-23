from django.utils import timezone

from logros.conditions import is_condition_met
from logros.models import Achievement, UserAchievement


def get_achievements_for_user(user):
    """Lista de logros con estado desbloqueado para la UI."""
    unlocked_ids = set(
        UserAchievement.objects.filter(user=user).values_list('achievement_id', flat=True)
    )
    achievements = Achievement.objects.filter(is_active=True)
    result = []
    for achievement in achievements:
        result.append({
            'achievement': achievement,
            'unlocked': achievement.id in unlocked_ids,
        })
    return result


def check_and_unlock_for_user(user):
    """
    Evalúa condiciones y persiste logros nuevos.
    Retorna los Achievement recién desbloqueados.
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
        if is_condition_met(user, achievement):
            UserAchievement.objects.create(user=user, achievement=achievement)
            newly_unlocked.append(achievement)

    return newly_unlocked


def get_pending_celebrations(user):
    """Logros desbloqueados cuyo modal de celebración aún no se mostró."""
    return list(
        UserAchievement.objects.filter(
            user=user,
            celebration_shown=False,
        )
        .select_related('achievement')
        .order_by('unlocked_at')
    )


def mark_celebrations_shown(user, achievement_slugs=None):
    qs = UserAchievement.objects.filter(user=user, celebration_shown=False)
    if achievement_slugs is not None:
        qs = qs.filter(achievement__slug__in=achievement_slugs)
    qs.update(celebration_shown=True, updated_at=timezone.now())
