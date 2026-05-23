from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.cache import cache
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods

from user_profile.services.sede_mercadopago import run_match_audit


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


@staff_member_required
@require_http_methods(['GET', 'POST'])
def admin_sede_matches_view(request):
    if request.method == 'POST':
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
        },
    )
