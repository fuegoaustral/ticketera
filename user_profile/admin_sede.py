import json

from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from user_profile.models import Profile
from user_profile.services.sede_mercadopago import (
    format_payment_method,
    get_sede_plan_ids,
    process_next_subscription,
    start_sync,
)


@staff_member_required
def admin_sede_view(request):
    members = (
        Profile.objects.filter(miembro_sede=True)
        .select_related('user')
        .order_by('user__last_name', 'user__first_name')
    )
    return render(request, 'admin/admin_sede.html', {
        'title': 'La Sede — Miembros',
        'members': members,
        'plan_ids': get_sede_plan_ids(),
    })


@staff_member_required
@require_POST
def admin_sede_sync_start(request):
    try:
        sync_id, total = start_sync()
        return JsonResponse({'sync_id': sync_id, 'total': total})
    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=500)


@staff_member_required
@require_POST
def admin_sede_sync_process(request):
    try:
        body = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    sync_id = body.get('sync_id')
    if not sync_id:
        return JsonResponse({'error': 'sync_id required'}, status=400)

    try:
        result = process_next_subscription(sync_id)
        if result.get('error'):
            return JsonResponse(result, status=400)
        return JsonResponse(result)
    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=500)
