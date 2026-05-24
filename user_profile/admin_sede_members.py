from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from user_profile.models import Profile, SedeSubscription, SedeSubscriptionPlan
from user_profile.services.sede_mercadopago import format_payment_method


def _attach_plan_billing_cycle(subscriptions, plan_billing_cycles):
    for subscription in subscriptions:
        subscription.billing_cycle = plan_billing_cycles.get(subscription.plan_id, '')
    return subscriptions


@staff_member_required
@require_http_methods(['GET', 'POST'])
def admin_sede_members_view(request):
    if request.method == 'POST':
        action = request.POST.get('action') or ''
        subscription_id = request.POST.get('subscription_id') or ''
        is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
        if action == 'soft_remove' and subscription_id:
            updated = SedeSubscription.objects.filter(
                subscription_id=subscription_id,
                is_soft_removed=False,
            ).update(
                is_soft_removed=True,
                is_active=False,
                status='removed',
                soft_removed_at=timezone.now(),
                synced_at=timezone.now(),
            )
            if is_ajax:
                return JsonResponse(
                    {
                        'ok': bool(updated),
                        'subscription_id': subscription_id,
                        'state_label': '[soft removed]',
                        'message': (
                            f'Subscription {subscription_id} was soft-removed from member sync.'
                            if updated
                            else f'Subscription {subscription_id} was already removed or does not exist.'
                        ),
                    },
                    status=200 if updated else 400,
                )
            if updated:
                messages.success(request, f'Subscription {subscription_id} was soft-removed from member sync.')
            else:
                messages.warning(request, f'Subscription {subscription_id} was already removed or does not exist.')
        else:
            if is_ajax:
                return JsonResponse({'ok': False, 'message': 'Invalid soft-remove request.'}, status=400)
            messages.error(request, 'Invalid soft-remove request.')
        return redirect('admin_sede_members_view')

    profiles = (
        Profile.objects.filter(sede_subscriptions__isnull=False)
        .select_related('user')
        .prefetch_related('sede_subscriptions')
        .distinct()
        .order_by('user__last_name', 'user__first_name', 'user__email')
    )

    plan_billing_cycles = {
        plan_id: billing_cycle
        for plan_id, billing_cycle in SedeSubscriptionPlan.objects.values_list('plan_id', 'billing_cycle')
    }

    rows = []
    for profile in profiles:
        subs = list(
            profile.sede_subscriptions.all().order_by('is_soft_removed', '-is_active', '-last_payment_date', '-synced_at')
        )
        _attach_plan_billing_cycle(subs, plan_billing_cycles)
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


@staff_member_required
@require_http_methods(['GET'])
def admin_sede_multiple_active_view(request):
    profiles = (
        Profile.objects.filter(sede_subscriptions__isnull=False, sede_subscriptions__is_soft_removed=False)
        .annotate(
            active_count=Count(
                'sede_subscriptions',
                filter=Q(sede_subscriptions__is_active=True, sede_subscriptions__is_soft_removed=False),
            )
        )
        .filter(active_count__gt=1)
        .select_related('user')
        .prefetch_related('sede_subscriptions')
        .order_by('-active_count', 'user__last_name', 'user__first_name', 'user__email')
    )

    plan_billing_cycles = {
        plan_id: billing_cycle
        for plan_id, billing_cycle in SedeSubscriptionPlan.objects.values_list('plan_id', 'billing_cycle')
    }

    rows = []
    for profile in profiles:
        subs = list(
            profile.sede_subscriptions.filter(is_soft_removed=False).order_by('-is_active', '-last_payment_date', '-synced_at')
        )
        _attach_plan_billing_cycle(subs, plan_billing_cycles)
        rows.append({
            'profile': profile,
            'subscriptions': subs,
            'active_count': profile.active_count,
        })

    return render(
        request,
        'admin/admin_sede_multiple_active.html',
        {
            'title': 'La Sede — Multiple Active Subscriptions',
            'rows': rows,
            'sede_section': 'multiple_active',
            'format_payment_method': format_payment_method,
        },
    )
