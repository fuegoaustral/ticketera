from django.contrib.auth.models import User
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from user_profile.models import SedeUnmatchedSubscription
from user_profile.services.sede_mercadopago import apply_subscription_to_profile


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


def _build_manual_details(unmatched):
    return {
        'subscription_id': unmatched.subscription_id,
        'plan_id': unmatched.plan_id or '',
        'tier_name': unmatched.tier_name or '',
        'status': unmatched.status or '',
        'payment_method': unmatched.payment_method or '',
        'last_payment_date': unmatched.last_payment_date,
        'last_payment_amount': unmatched.last_payment_amount,
        'next_payment_date': unmatched.next_payment_date,
        'member_since': unmatched.member_since,
    }


@staff_member_required
@require_http_methods(['GET'])
def admin_sede_matches_view(request):
    unmatched_rows = (
        SedeUnmatchedSubscription.objects.filter(status='authorized')
        .order_by('-last_seen_at', '-updated_at')[:500]
    )
    return render(
        request,
        'admin/admin_sede_matches.html',
        {
            'title': 'La Sede — Unmatched Active Subscriptions',
            'unmatched_rows': unmatched_rows,
            'sede_section': 'matches',
        },
    )


@staff_member_required
@require_http_methods(['GET'])
def admin_sede_matches_user_search(request):
    query = (request.GET.get('q') or '').strip()
    users = _search_users(query) if query else []
    payload = [
        {
            'id': user.id,
            'full_name': (user.get_full_name() or '').strip(),
            'email': user.email,
            'document_number': user.profile.document_number if getattr(user, 'profile', None) else '',
            'miembro_sede': bool(user.profile.miembro_sede) if getattr(user, 'profile', None) else False,
        }
        for user in users
    ]
    return JsonResponse({'results': payload})


@staff_member_required
@require_http_methods(['POST'])
def admin_sede_matches_assign(request):
    unmatched_id = request.POST.get('unmatched_id')
    user_id = request.POST.get('user_id')
    if not unmatched_id or not user_id:
        return JsonResponse({'ok': False, 'error': 'unmatched_id and user_id are required'}, status=400)

    unmatched = SedeUnmatchedSubscription.objects.filter(id=unmatched_id).first()
    if not unmatched:
        return JsonResponse({'ok': False, 'error': 'Unmatched subscription not found'}, status=404)
    if (unmatched.status or '').lower() != 'authorized':
        return JsonResponse({'ok': False, 'error': 'Only authorized subscriptions can be matched manually'}, status=400)

    user = User.objects.select_related('profile').filter(id=user_id, profile__isnull=False).first()
    if not user:
        return JsonResponse({'ok': False, 'error': 'User not found or missing profile'}, status=404)

    details = _build_manual_details(unmatched)
    apply_subscription_to_profile(user.profile, details, match_method='manual')
    subscription_id = unmatched.subscription_id
    unmatched.delete()

    return JsonResponse(
        {
            'ok': True,
            'message': f'Subscription {subscription_id} matched to {user.email}',
        }
    )
