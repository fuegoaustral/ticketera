from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.cache import cache
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods

from user_profile.services.sede_mercadopago import run_match_audit


CACHE_KEY = 'sede_match_audit_latest_v1'
CACHE_TTL_SECONDS = 60 * 60


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
    return render(
        request,
        'admin/admin_sede_matches.html',
        {
            'title': 'La Sede — Match Audit',
            'report': report,
            'summary': (report or {}).get('summary') or {},
            'rows': (report or {}).get('rows') or [],
            'generated_at': (report or {}).get('generated_at'),
        },
    )
