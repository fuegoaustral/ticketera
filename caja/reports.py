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


ORDER_TYPE_LABELS = {
    'ONLINE_PURCHASE': 'Compra online',
    'CASH_ONSITE': 'Efectivo (caja)',
    'LOCAL_TRANSFER': 'Transferencia (caja)',
    'INTERNATIONAL_TRANSFER': 'Transferencia internacional',
    'MP_QR_CAJA': 'Mercado Pago QR (caja v2)',
    'MP_POINT_CAJA': 'Mercado Pago Postnet (caja v2)',
    'OTHER': 'Otro',
}


def _line_revenue(lines_qs):
    total_qty = 0
    total_revenue = Decimal('0')
    by_product = defaultdict(lambda: {'quantity': 0, 'revenue': Decimal('0')})
    for line in lines_qs.values('event_product__name', 'quantity', 'unit_price'):
        name = line['event_product__name'] or 'Sin nombre'
        qty = line['quantity'] or 0
        price = line['unit_price'] or Decimal('0')
        revenue = price * qty
        total_qty += qty
        total_revenue += revenue
        by_product[name]['quantity'] += qty
        by_product[name]['revenue'] += revenue
    products = sorted(
        [
            {'name': name, 'quantity': data['quantity'], 'revenue': data['revenue']}
            for name, data in by_product.items()
        ],
        key=lambda row: row['revenue'],
        reverse=True,
    )
    return total_qty, total_revenue, products


def build_event_report(event):
    from tickets.models import NewTicket, Order

    orders = Order.objects.filter(
        event=event,
        status=Order.OrderStatus.CONFIRMED,
    )
    order_agg = orders.aggregate(
        total_bruto=Sum('amount'),
        total_neto=Sum('net_received_amount'),
        donations_art=Sum('donation_art'),
        donations_venue=Sum('donation_venue'),
        donations_grant=Sum('donation_grant'),
        order_count=Count('id'),
    )

    total_bruto = order_agg['total_bruto'] or Decimal('0')
    total_neto = order_agg['total_neto'] or Decimal('0')
    donations_art = order_agg['donations_art'] or Decimal('0')
    donations_venue = order_agg['donations_venue'] or Decimal('0')
    donations_grant = order_agg['donations_grant'] or Decimal('0')
    donations_total = donations_art + donations_venue + donations_grant
    ticket_revenue_orders = total_bruto - donations_total
    commissions_total = (total_bruto - total_neto).quantize(Decimal('0.01'))

    tickets_qs = NewTicket.objects.filter(
        event=event,
        order__status=Order.OrderStatus.CONFIRMED,
    )
    tickets_total = tickets_qs.count()
    tickets_online = tickets_qs.filter(order__generated_by_admin_user__isnull=True).count()
    tickets_caja = tickets_total - tickets_online
    tickets_used = tickets_qs.filter(is_used=True).count()

    online_orders = orders.filter(generated_by_admin_user__isnull=True)
    caja_orders = orders.filter(generated_by_admin_user__isnull=False)
    online_agg = online_orders.aggregate(
        bruto=Sum('amount'),
        neto=Sum('net_received_amount'),
        count=Count('id'),
    )
    caja_orders_agg = caja_orders.aggregate(
        bruto=Sum('amount'),
        neto=Sum('net_received_amount'),
        count=Count('id'),
    )

    mp_online = online_orders.filter(order_type=Order.OrderType.ONLINE_PURCHASE).aggregate(
        bruto=Sum('amount'),
        neto=Sum('net_received_amount'),
    )
    mp_bruto = mp_online['bruto'] or Decimal('0')
    mp_neto = mp_online['neto'] or Decimal('0')
    mp_commissions = (mp_bruto - mp_neto).quantize(Decimal('0.01'))
    mp_pct = (
        ((Decimal('1') - (mp_neto / mp_bruto)) * 100).quantize(Decimal('0.01'))
        if mp_bruto > 0 else Decimal('0')
    )

    payment_breakdown = []
    for row in orders.values('order_type').annotate(
        bruto=Sum('amount'),
        neto=Sum('net_received_amount'),
        ordenes=Count('id'),
        don_art=Sum('donation_art'),
        don_venue=Sum('donation_venue'),
        don_grant=Sum('donation_grant'),
    ).order_by('order_type'):
        payment_breakdown.append({
            'label': ORDER_TYPE_LABELS.get(row['order_type'], row['order_type']),
            'order_type': row['order_type'],
            'bruto': row['bruto'] or Decimal('0'),
            'neto': row['neto'] or Decimal('0'),
            'ordenes': row['ordenes'] or 0,
            'donaciones': (
                (row['don_art'] or Decimal('0'))
                + (row['don_venue'] or Decimal('0'))
                + (row['don_grant'] or Decimal('0'))
            ),
        })

    paid_caja_lines = CajaSaleLine.objects.filter(
        caja_sale__event_caja__event=event,
        caja_sale__status=CajaSale.Status.PAID,
    )
    ticket_lines = paid_caja_lines.filter(event_product__ticket_type_id__isnull=False)
    generic_lines = paid_caja_lines.filter(event_product__ticket_type_id__isnull=True)

    _, caja_v2_ticket_revenue, caja_v2_ticket_products = _line_revenue(ticket_lines)
    generic_qty, generic_revenue, generic_products = _line_revenue(generic_lines)

    caja_v2_sales = _paid_sales(event)
    caja_v2_agg = caja_v2_sales.aggregate(
        revenue=Sum('total_amount'),
        sales=Count('id'),
    )
    caja_v2_by_payment = list(
        caja_v2_sales.values('payment_method').annotate(
            revenue=Sum('total_amount'),
            count=Count('id'),
        ).order_by('-revenue')
    )
    caja_payment_labels = dict(CajaSale.PaymentMethod.choices)
    for row in caja_v2_by_payment:
        row['label'] = caja_payment_labels.get(row['payment_method'], row['payment_method'])

    grand_bruto = total_bruto + generic_revenue
    grand_neto = total_neto + generic_revenue

    return {
        'summary': {
            'tickets_total': tickets_total,
            'tickets_online': tickets_online,
            'tickets_caja': tickets_caja,
            'tickets_used': tickets_used,
            'orders_count': order_agg['order_count'] or 0,
            'ticket_revenue': ticket_revenue_orders,
            'donations_total': donations_total,
            'donations_art': donations_art,
            'donations_venue': donations_venue,
            'donations_grant': donations_grant,
            'orders_bruto': total_bruto,
            'orders_neto': total_neto,
            'commissions_total': commissions_total,
            'mp_commissions': mp_commissions,
            'mp_pct': mp_pct,
            'generic_qty': generic_qty,
            'generic_revenue': generic_revenue,
            'caja_v2_sales': caja_v2_agg['sales'] or 0,
            'caja_v2_revenue': caja_v2_agg['revenue'] or Decimal('0'),
            'caja_v2_ticket_revenue': caja_v2_ticket_revenue,
            'grand_bruto': grand_bruto,
            'grand_neto': grand_neto,
            'venue_occupancy': event.venue_occupancy,
            'venue_capacity': event.venue_capacity,
            'occupancy_percentage': event.occupancy_percentage,
        },
        'online_orders': {
            'bruto': online_agg['bruto'] or Decimal('0'),
            'neto': online_agg['neto'] or Decimal('0'),
            'count': online_agg['count'] or 0,
        },
        'caja_orders': {
            'bruto': caja_orders_agg['bruto'] or Decimal('0'),
            'neto': caja_orders_agg['neto'] or Decimal('0'),
            'count': caja_orders_agg['count'] or 0,
        },
        'payment_breakdown': payment_breakdown,
        'generic_products': generic_products,
        'caja_v2_by_payment': caja_v2_by_payment,
        'caja_v2_ticket_products': caja_v2_ticket_products,
    }
