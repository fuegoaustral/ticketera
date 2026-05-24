from django.contrib.auth.models import User
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from user_profile.models import SedeSubscription


MANUAL_GENERIC_MATCHED_VIA = 'manual_generic'
MANUAL_GENERIC_PLAN_ID = 'manual_generic'
MANUAL_GENERIC_TIER_NAME = 'Suscripcion generica'
STATUS_ACTIVE = 'active'
STATUS_INACTIVE = 'inactive'
ALLOWED_MANUAL_STATUSES = {STATUS_ACTIVE, STATUS_INACTIVE}


def _manual_subscription_id_for_profile(profile_id):
    return f'manual-generic-profile-{profile_id}'


def _search_users(query, limit=25):
    if not query:
        return []

    query = query.strip()
    if not query:
        return []

    users = (
        User.objects.select_related('profile')
        .filter(profile__isnull=False)
        .filter(
            Q(email__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(profile__document_number__icontains=query)
            | Q(emailaddress__email__icontains=query)
        )
        .distinct()
        .order_by('last_name', 'first_name', 'email')[:limit]
    )
    return list(users)


@staff_member_required
@require_http_methods(['GET'])
def admin_sede_manual_members_view(request):
    rows = (
        SedeSubscription.objects.filter(matched_via=MANUAL_GENERIC_MATCHED_VIA)
        .select_related('profile__user')
        .order_by('-is_active', 'profile__user__last_name', 'profile__user__first_name', 'profile__user__email')
    )
    return render(
        request,
        'admin/admin_sede_manual_members.html',
        {
            'title': 'La Sede - Manual Members',
            'rows': rows,
            'sede_section': 'manual_members',
        },
    )


@staff_member_required
@require_http_methods(['GET'])
def admin_sede_manual_members_user_search(request):
    query = (request.GET.get('q') or '').strip()
    users = _search_users(query) if query else []
    payload = []
    for user in users:
        profile = getattr(user, 'profile', None)
        manual_sub = None
        if profile:
            manual_sub = profile.sede_subscriptions.filter(
                matched_via=MANUAL_GENERIC_MATCHED_VIA,
                is_soft_removed=False,
            ).order_by('-is_active', '-updated_at').first()
        payload.append(
            {
                'id': user.id,
                'full_name': (user.get_full_name() or '').strip(),
                'email': user.email,
                'document_number': profile.document_number if profile else '',
                'manual_status': STATUS_ACTIVE if (manual_sub and manual_sub.is_active) else STATUS_INACTIVE if manual_sub else '',
                'manual_subscription_id': manual_sub.subscription_id if manual_sub else '',
                'miembro_sede': bool(profile.miembro_sede) if profile else False,
            }
        )
    return JsonResponse({'results': payload})


@staff_member_required
@require_http_methods(['POST'])
def admin_sede_manual_members_assign(request):
    user_id = request.POST.get('user_id')
    requested_status = (request.POST.get('manual_status') or '').strip().lower()
    if not user_id:
        return JsonResponse({'ok': False, 'error': 'user_id is required'}, status=400)
    if requested_status not in ALLOWED_MANUAL_STATUSES:
        return JsonResponse({'ok': False, 'error': 'manual_status must be active or inactive'}, status=400)

    user = User.objects.select_related('profile').filter(id=user_id, profile__isnull=False).first()
    if not user:
        return JsonResponse({'ok': False, 'error': 'User not found or missing profile'}, status=404)

    profile = user.profile
    now = timezone.now()
    is_active = requested_status == STATUS_ACTIVE
    status_value = 'authorized' if is_active else 'inactive'

    subscription_id = _manual_subscription_id_for_profile(profile.id)
    subscription, _ = SedeSubscription.objects.get_or_create(
        subscription_id=subscription_id,
        defaults={
            'profile': profile,
            'matched_via': MANUAL_GENERIC_MATCHED_VIA,
        },
    )

    if subscription.matched_via != MANUAL_GENERIC_MATCHED_VIA:
        return JsonResponse({'ok': False, 'error': 'Subscription id collision with non-manual record'}, status=409)

    subscription.profile = profile
    subscription.plan_id = MANUAL_GENERIC_PLAN_ID
    subscription.tier_name = MANUAL_GENERIC_TIER_NAME
    subscription.status = status_value
    subscription.payment_method = ''
    subscription.is_active = is_active
    subscription.is_soft_removed = False
    subscription.soft_removed_at = None
    subscription.synced_at = now
    subscription.next_payment_date = None
    subscription.last_payment_date = None
    subscription.last_payment_amount = None
    if is_active and not subscription.member_since:
        subscription.member_since = now
    subscription.matched_via = MANUAL_GENERIC_MATCHED_VIA
    subscription.save()

    return JsonResponse(
        {
            'ok': True,
            'message': f'Membresia manual actualizada para {user.email}',
            'subscription_id': subscription.subscription_id,
            'manual_status': requested_status,
            'status_label': 'Activa' if is_active else 'Inactiva',
        }
    )
