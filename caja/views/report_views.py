from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, render

from caja.context import mi_fuego_admin_context
from caja.permissions import user_is_event_admin
from caja.reports import build_caja_sales_report, build_event_report, build_stock_report
from events.models import Event


@login_required
def caja_sales_report_view(request, event_slug):
    event = get_object_or_404(Event, slug=event_slug)
    if not user_is_event_admin(request.user, event):
        return HttpResponseForbidden('No tenés permiso para ver este evento')

    report = build_caja_sales_report(event)
    context = mi_fuego_admin_context(request, event, f'caja_sales_report_{event.slug}')
    context.update({
        'report': report,
        'has_sales': report['summary']['total_sales'] > 0,
    })
    return render(request, 'mi_fuego/caja_v2/caja_sales_report.html', context)


@login_required
def event_report_view(request, event_slug):
    event = get_object_or_404(Event, slug=event_slug)
    if not user_is_event_admin(request.user, event):
        return HttpResponseForbidden('No tenés permiso para ver este evento')

    report = build_event_report(event)
    context = mi_fuego_admin_context(request, event, f'event_report_{event.slug}')
    context.update({'report': report})
    return render(request, 'mi_fuego/caja_v2/event_report.html', context)


@login_required
def stock_report_view(request, event_slug):
    event = get_object_or_404(Event, slug=event_slug)
    if not user_is_event_admin(request.user, event):
        return HttpResponseForbidden('No tenés permiso para ver este evento')

    report = build_stock_report(event)
    context = mi_fuego_admin_context(request, event, f'caja_stock_report_{event.slug}')
    context.update({
        'report': report,
    })
    return render(request, 'mi_fuego/caja_v2/stock_report.html', context)
