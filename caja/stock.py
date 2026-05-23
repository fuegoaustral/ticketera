from django.db import transaction
from django.db.models import OuterRef, Q, Subquery

from caja.models import EventProduct, EventProductStock, EventProductStockRecord


class InsufficientStockError(Exception):
    def __init__(self, product, requested, available):
        self.product = product
        self.requested = requested
        self.available = available
        super().__init__(
            f'Stock insuficiente para {product.display_name}: '
            f'solicitados {requested}, disponibles {available}'
        )


def ensure_stock_row(product):
    stock, _ = EventProductStock.objects.get_or_create(event_product=product)
    return stock


def available(product):
    stock = ensure_stock_row(product)
    return stock.quantity


def available_for_ticket_type(ticket_type):
    product = EventProduct.objects.filter(ticket_type=ticket_type).first()
    if product:
        return available(product)
    return ticket_type.ticket_count


def ticket_type_has_stock(ticket_type):
    qty = available_for_ticket_type(ticket_type)
    return qty is None or qty > 0


def ticket_types_with_stock_queryset(queryset):
    stock_subq = EventProductStock.objects.filter(
        event_product__ticket_type=OuterRef('pk'),
    ).values('quantity')[:1]
    return queryset.annotate(stock_quantity=Subquery(stock_subq)).filter(
        Q(stock_quantity__gt=0) | Q(stock_quantity__isnull=True)
    )


def get_or_create_product_for_ticket_type(ticket_type, initial_quantity=None):
    product, created = EventProduct.objects.get_or_create(
        ticket_type=ticket_type,
        defaults={
            'event_id': ticket_type.event_id,
            'name': ticket_type.name,
            'price': ticket_type.price or 0,
        },
    )
    stock = ensure_stock_row(product)
    if created or stock.quantity is None and initial_quantity is not None:
        if stock.quantity is None and not EventProductStockRecord.objects.filter(
            event_product=product,
            reason=EventProductStockRecord.Reason.MIGRATION,
        ).exists():
            qty = initial_quantity if initial_quantity is not None else ticket_type.ticket_count
            _apply_delta(
                product,
                qty,
                EventProductStockRecord.Reason.MIGRATION,
                user=None,
                notes='Migración desde ticket_count',
            )
    return product


def _apply_delta(product, delta, reason, user=None, notes='', caja_sale=None, order=None):
    stock = EventProductStock.objects.select_for_update().get(event_product=product)
    if stock.quantity is not None:
        new_balance = stock.quantity + delta
        if new_balance < 0:
            raise InsufficientStockError(product, abs(delta), stock.quantity)
        stock.quantity = new_balance
        stock.save(update_fields=['quantity', 'updated_at'])
        balance_after = new_balance
    else:
        balance_after = None

    EventProductStockRecord.objects.create(
        event_product=product,
        delta=delta,
        reason=reason,
        balance_after=balance_after,
        notes=notes,
        created_by=user,
        caja_sale=caja_sale,
        order=order,
    )
    return stock


def adjust_stock(product, delta, user, notes=''):
    with transaction.atomic():
        return _apply_delta(
            product,
            delta,
            EventProductStockRecord.Reason.ADMIN_ADJUST,
            user=user,
            notes=notes,
        )


def initialize_product_stock(product, *, unlimited=False, initial_quantity=None, user=None):
    ensure_stock_row(product)
    if unlimited:
        set_unlimited(product, True, user=user)
        return
    set_unlimited(product, False, user=user)
    if initial_quantity and initial_quantity > 0:
        adjust_stock(product, initial_quantity, user, notes='Stock inicial')


def set_unlimited(product, unlimited, user=None):
    with transaction.atomic():
        stock = EventProductStock.objects.select_for_update().get(event_product=product)
        if unlimited:
            stock.quantity = None
        elif stock.quantity is None:
            stock.quantity = 0
        stock.save(update_fields=['quantity', 'updated_at'])
        EventProductStockRecord.objects.create(
            event_product=product,
            delta=0,
            reason=EventProductStockRecord.Reason.ADMIN_ADJUST,
            balance_after=stock.quantity,
            notes='Stock ilimitado activado' if unlimited else 'Stock ilimitado desactivado',
            created_by=user,
        )


def validate_lines(lines):
    for line in lines:
        product = line['event_product']
        quantity = line['quantity']
        qty = available(product)
        if qty is not None and quantity > qty:
            raise InsufficientStockError(product, quantity, qty)


def deduct_for_lines(lines, user, reason, caja_sale=None, order=None, notes=''):
    with transaction.atomic():
        validate_lines(lines)
        for line in lines:
            if line['quantity'] <= 0:
                continue
            _apply_delta(
                line['event_product'],
                -line['quantity'],
                reason,
                user=user,
                notes=notes,
                caja_sale=caja_sale,
                order=order,
            )


def deduct_for_order_tickets(order, user=None):
    from tickets.models import OrderTicket

    lines = []
    order_tickets = OrderTicket.objects.filter(order=order).select_related('ticket_type')
    for ot in order_tickets:
        product = get_or_create_product_for_ticket_type(ot.ticket_type)
        lines.append({'event_product': product, 'quantity': ot.quantity})

    if not lines:
        return

    deduct_for_lines(
        lines,
        user=user or order.user,
        reason=EventProductStockRecord.Reason.ORDER_MINT,
        order=order,
        notes=f'Orden {order.key}',
    )


def door_stock_for_event(event):
    from tickets.models import TicketType

    total = 0
    has_unlimited = False
    ticket_types = TicketType.objects.filter(
        event=event,
        show_in_caja=True,
        is_direct_type=False,
    )
    for tt in ticket_types:
        qty = available_for_ticket_type(tt)
        if qty is None:
            has_unlimited = True
        else:
            total += max(0, qty)
    if has_unlimited:
        return None
    return total
