from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.admin.views.decorators import staff_member_required
from django.core.cache import cache
from django.db.models import Q
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods

from user_profile.models import SedeUnmatchedSubscription
from user_profile.services.sede_mercadopago import run_match_audit, apply_subscription_to_profile


CACHE_KEY = 'sede_match_audit_latest_v1'
CACHE_TTL_SECONDS = 60 * 60


def _build_user_rows(rows):
    users = {}
    for row in rows:
        if not row.get('matched'):
            continue
        user_id = row.get('user_id')
        user_email = row.get('user_email')
        if not user_id or not user_email:
            continue

        item = users.setdefault(
            user_id,
            {
                'user_email': user_email,
                'active_count': 0,
                'matched_count': 0,
                'plans': set(),
                'tiers': set(),
            },
        )
        item['matched_count'] += 1
        if row.get('status') == 'authorized':
            item['active_count'] += 1
        plan_id = row.get('plan_id')
        tier_name = row.get('tier_name')
        if plan_id and plan_id != '—':
            item['plans'].add(plan_id)
        if tier_name and tier_name != '—':
            item['tiers'].add(tier_name)

    user_rows = []
    for item in users.values():
        user_rows.append(
            {
                'user_email': item['user_email'],
                'active_count': item['active_count'],
                'matched_count': item['matched_count'],
                'plans': ', '.join(sorted(item['plans'])) or '—',
                'tiers': ', '.join(sorted(item['tiers'])) or '—',
            }
        )

    user_rows.sort(key=lambda x: (-x['active_count'], -x['matched_count'], x['user_email']))
    return user_rows


def _search_users(query, limit=50):
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
@require_http_methods(['GET', 'POST'])
def admin_sede_matches_view(request):
    if request.method == 'POST':
        action = request.POST.get('action') or ''
        if action == 'assign_unmatched':
            unmatched_id = request.POST.get('unmatched_id')
            user_id = request.POST.get('user_id')
            search_query = request.POST.get('search_query', '')
            try:
                unmatched = SedeUnmatchedSubscription.objects.get(id=unmatched_id)
            except SedeUnmatchedSubscription.DoesNotExist:
                messages.error(request, 'No se encontró la suscripción no matcheada.')
                return redirect('admin_sede_matches_view')

            try:
                user = User.objects.select_related('profile').get(id=user_id)
            except User.DoesNotExist:
                messages.error(request, 'No se encontró el usuario seleccionado.')
                return redirect(f"{request.path}?selected_unmatched={unmatched.id}&user_q={search_query}")

            profile = getattr(user, 'profile', None)
            if not profile:
                messages.error(request, 'El usuario no tiene profile asociado.')
                return redirect(f"{request.path}?selected_unmatched={unmatched.id}&user_q={search_query}")

            details = _build_manual_details(unmatched)
            apply_subscription_to_profile(profile, details, match_method='manual')
            unmatched.delete()
            messages.success(
                request,
                f'Suscripción {details["subscription_id"]} vinculada manualmente a {user.email}.',
            )
            return redirect('admin_sede_matches_view')

        report = run_match_audit()
        if report.get('error'):
            messages.error(request, report['error'])
        else:
            cache.set(CACHE_KEY, report, CACHE_TTL_SECONDS)
            summary = report.get('summary') or {}
            messages.success(
                request,
                (
                    f"Audit listo: {summary.get('matched', 0)} match, "
                    f"{summary.get('unmatched', 0)} sin match, "
                    f"{summary.get('conflicts', 0)} conflictos, "
                    f"{summary.get('errors', 0)} errores."
                ),
            )
        return redirect('admin_sede_matches_view')

    report = cache.get(CACHE_KEY)
    rows = (report or {}).get('rows') or []
    user_rows = _build_user_rows(rows)
    unmatched_rows = SedeUnmatchedSubscription.objects.order_by('-last_seen_at', '-updated_at')[:200]
    selected_unmatched_id = request.GET.get('selected_unmatched')
    selected_unmatched = None
    if selected_unmatched_id:
        selected_unmatched = SedeUnmatchedSubscription.objects.filter(id=selected_unmatched_id).first()
    if selected_unmatched is None and unmatched_rows:
        selected_unmatched = unmatched_rows[0]

    user_q = request.GET.get('user_q', '').strip()
    user_candidates = _search_users(user_q) if user_q else []
    return render(
        request,
        'admin/admin_sede_matches.html',
        {
            'title': 'La Sede — Match Audit',
            'report': report,
            'summary': (report or {}).get('summary') or {},
            'rows': rows,
            'user_rows': user_rows,
            'generated_at': (report or {}).get('generated_at'),
            'unmatched_rows': unmatched_rows,
            'selected_unmatched': selected_unmatched,
            'user_q': user_q,
            'user_candidates': user_candidates,
        },
    )
