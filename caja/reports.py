from collections import defaultdict

import json
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import TruncHour
from django.utils import timezone

from caja.models import CajaSale, CajaSaleLine, EventProduct


def _paid_sales(event):
    return CajaSale.objects.filter(
        event_caja__event=event,
        status=CajaSale.Status.PAID,
    )


def _chart_rows(queryset, label_field, value_fields):
    rows = []
    for row in queryset:
        label = row.get(label_field) or 'Sin nombre'
        entry = {'label': str(label)}
        for key, field in value_fields.items():
            value = row.get(field) or 0
            entry[key] = float(value) if isinstance(value, Decimal) else value
        rows.append(entry)
    return rows


def build_caja_sales_report(event):
    sales = _paid_sales(event)
    summary = sales.aggregate(
        total_revenue=Sum('total_amount'),
        total_sales=Count('id'),
        items_sold=Sum('lines__quantity'),
    )

    by_caja_qs = (
        sales.values('event_caja__name')
        .annotate(
            revenue=Sum('total_amount'),
            count=Count('id', distinct=True),
        )
        .order_by('-revenue')
    )
    by_caja = list(by_caja_qs)

    by_item_lines = CajaSaleLine.objects.filter(
        caja_sale__event_caja__event=event,
        caja_sale__status=CajaSale.Status.PAID,
    ).values('event_product__name', 'quantity', 'unit_price')

    by_item_map = defaultdict(lambda: {'quantity': 0, 'revenue': Decimal('0')})
    for line in by_item_lines:
        name = line['event_product__name'] or 'Sin nombre'
        qty = line['quantity'] or 0
        price = line['unit_price'] or Decimal('0')
        by_item_map[name]['quantity'] += qty
        by_item_map[name]['revenue'] += price * qty

    by_item = sorted(
        [
            {
                'event_product__name': name,
                'quantity': data['quantity'],
                'revenue': data['revenue'],
            }
            for name, data in by_item_map.items()
        ],
        key=lambda row: row['revenue'],
        reverse=True,
    )

    by_hour_qs = (
        sales.annotate(hour=TruncHour('created_at'))
        .values('hour')
        .annotate(
            revenue=Sum('total_amount'),
            count=Count('id'),
        )
        .order_by('hour')
    )
    by_hour = []
    for row in by_hour_qs:
        hour = row['hour']
        by_hour.append({
            'hour': timezone.localtime(hour).strftime('%d/%m %H:%M') if hour else '—',
            'revenue': row['revenue'] or Decimal('0'),
            'count': row['count'] or 0,
        })

    by_payment = list(
        sales.values('payment_method')
        .annotate(revenue=Sum('total_amount'), count=Count('id'))
        .order_by('-revenue')
    )
    payment_labels = dict(CajaSale.PaymentMethod.choices)
    for row in by_payment:
        row['label'] = payment_labels.get(row['payment_method'], row['payment_method'])

    stock_report = build_stock_report(event)
    stock_chart_rows = [
        {'label': row['name'], 'quantity': row['quantity']}
        for row in stock_report['rows']
        if row['quantity'] is not None and row['is_active']
    ]

    return {
        'summary': {
            'total_revenue': summary['total_revenue'] or Decimal('0'),
            'total_sales': summary['total_sales'] or 0,
            'items_sold': summary['items_sold'] or 0,
        },
        'by_caja': by_caja,
        'by_item': by_item,
        'by_hour': by_hour,
        'by_payment': by_payment,
        'stock_rows': stock_report['rows'][:20],
        'charts': {
            'by_caja': json.dumps(_chart_rows(by_caja, 'event_caja__name', {
                'revenue': 'revenue',
                'count': 'count',
            })),
            'by_item': json.dumps(_chart_rows(by_item, 'event_product__name', {
                'quantity': 'quantity',
                'revenue': 'revenue',
            })),
            'by_hour': json.dumps(_chart_rows(by_hour, 'hour', {
                'revenue': 'revenue',
                'count': 'count',
            })),
            'by_payment': json.dumps(_chart_rows(by_payment, 'label', {
                'revenue': 'revenue',
                'count': 'count',
            })),
            'stock': json.dumps(_chart_rows(stock_chart_rows, 'label', {
                'quantity': 'quantity',
            })),
        },
    }


def build_stock_report(event):
    products = (
        EventProduct.objects.filter(event=event)
        .select_related('ticket_type', 'stock')
        .order_by('ticket_type_id', 'name', 'id')
    )
    rows = []
    unlimited_count = 0
    low_stock_count = 0
    for product in products:
        stock = getattr(product, 'stock', None)
        quantity = stock.quantity if stock else None
        if quantity is None:
            unlimited_count += 1
            stock_label = 'Ilimitado'
        else:
            if quantity <= 5:
                low_stock_count += 1
            stock_label = str(quantity)
        rows.append({
            'product': product,
            'name': product.display_name,
            'is_ticket': bool(product.ticket_type_id),
            'is_active': product.is_active,
            'price': product.price or Decimal('0'),
            'quantity': quantity,
            'stock_label': stock_label,
        })
    return {
        'rows': rows,
        'summary': {
            'total_products': len(rows),
            'active_products': sum(1 for r in rows if r['is_active']),
            'unlimited_count': unlimited_count,
            'low_stock_count': low_stock_count,
        },
    }
