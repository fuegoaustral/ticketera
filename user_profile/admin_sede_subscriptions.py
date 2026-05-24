from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from user_profile.models import SedeSubscriptionPlan
from user_profile.sede_sync_cron import dispatch_sede_members_sync
from user_profile.services.sede_mercadopago import refresh_sede_subscription_plans


@staff_member_required
@require_http_methods(['GET', 'POST'])
def admin_sede_subscriptions_view(request):
    if request.method == 'POST':
        action = request.POST.get('action') or ''

        if action == 'refresh':
            try:
                summary = refresh_sede_subscription_plans()
                messages.success(
                    request,
                    (
                        f"Refresh listo: {summary.get('refreshed_plans', 0)} plan(es) "
                        f"desde {summary.get('total_subscriptions', 0)} suscripción(es)."
                    ),
                )
            except Exception as exc:
                messages.error(request, f'Error refrescando suscripciones: {exc}')
            return redirect('admin_sede_subscriptions_view')

        if action == 'toggle_plan':
            plan_id = request.POST.get('plan_id') or ''
            enabled = request.POST.get('enabled') == '1'
            plan = SedeSubscriptionPlan.objects.filter(plan_id=plan_id).first()
            if not plan:
                messages.error(request, f'Plan no encontrado: {plan_id}')
            else:
                plan.is_enabled = enabled
                plan.save(update_fields=['is_enabled', 'updated_at'])
                state = 'habilitado' if enabled else 'deshabilitado'
                messages.success(request, f'Plan {plan.plan_id} {state}.')
            return redirect('admin_sede_subscriptions_view')

        if action == 'run_sync_now':
            try:
                result = dispatch_sede_members_sync()
                if result.get('queued'):
                    messages.success(
                        request,
                        'Sync La Sede disparado en background (Zappa task). Revisá logs para seguimiento.',
                    )
                else:
                    summary = result.get('summary') or {}
                    messages.success(
                        request,
                        (
                            f"Sync terminado: {summary.get('matched', 0)} match, "
                            f"{summary.get('unmatched', 0)} sin match, "
                            f"{summary.get('conflicts', 0)} conflictos, "
                            f"{summary.get('errors', 0)} errores."
                        ),
                    )
            except Exception as exc:
                messages.error(request, f'Error al disparar sync: {exc}')
            return redirect('admin_sede_subscriptions_view')

    plans = SedeSubscriptionPlan.objects.order_by('-is_enabled', 'plan_name', 'plan_id')
    return render(
        request,
        'admin/admin_sede_subscriptions.html',
        {
            'title': 'La Sede — Subscription Plans',
            'plans': plans,
            'sede_section': 'subscriptions',
        },
    )
