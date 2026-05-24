from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from user_profile.models import Profile
from user_profile.services.sede_mercadopago import format_payment_method


@staff_member_required
@require_http_methods(['GET'])
def admin_sede_members_view(request):
    profiles = (
        Profile.objects.filter(sede_subscriptions__isnull=False)
        .select_related('user')
        .prefetch_related('sede_subscriptions')
        .distinct()
        .order_by('user__last_name', 'user__first_name', 'user__email')
    )

    rows = []
    for profile in profiles:
        subs = list(profile.sede_subscriptions.all().order_by('-is_active', '-last_payment_date', '-synced_at'))
        rows.append({
            'profile': profile,
            'subscriptions': subs,
            'active_count': sum(1 for sub in subs if sub.is_active),
        })

    return render(
        request,
        'admin/admin_sede_members.html',
        {
            'title': 'La Sede — Matched Members',
            'rows': rows,
            'sede_section': 'members',
            'format_payment_method': format_payment_method,
        },
    )
