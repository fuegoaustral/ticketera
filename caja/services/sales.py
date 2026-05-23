import logging
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from caja.models import CajaSale, CajaSaleLine, EventProductStockRecord
from caja.stock import InsufficientStockError, deduct_for_lines, validate_lines
from tickets.models import NewTicket, Order, OrderTicket

User = get_user_model()
logger = logging.getLogger(__name__)


def _order_type_for_payment(payment_method):
    mapping = {
        CajaSale.PaymentMethod.EFECTIVO: Order.OrderType.CASH_ONSITE,
        CajaSale.PaymentMethod.TRANSFERENCIA: Order.OrderType.LOCAL_TRANSFER,
        CajaSale.PaymentMethod.MP_QR: Order.OrderType.MP_QR_CAJA,
        CajaSale.PaymentMethod.MP_POINT: Order.OrderType.MP_POINT_CAJA,
    }
    return mapping[payment_method]


def _send_password_reset_email(user):
    from allauth.account.forms import ResetPasswordForm

    reset_form = ResetPasswordForm(data={'email': user.email.lower()})
    if reset_form.is_valid():
        reset_form.save(
            subject_template_name='account/email/password_reset_key_subject.txt',
            email_template_name='account/email/password_reset_key_message.html',
            from_email=settings.DEFAULT_FROM_EMAIL,
            request=None,
            use_https=False,
            html_email_template_name=None,
            extra_email_context=None,
        )


def _create_or_get_customer(email):
    """Returns (user, created) — same account setup as caja v1."""
    if not email:
        return None, False
    email = email.lower().strip()
    try:
        return User.objects.get(email=email), False
    except User.DoesNotExist:
        user = User.objects.create_user(
            username=str(uuid.uuid4()),
            email=email,
            first_name='',
            last_name='',
        )
        from user_profile.models import Profile

        if not hasattr(user, 'profile'):
            Profile.objects.create(user=user)
        user.profile.profile_completion = 'NONE'
        user.profile.save()
        from allauth.account.models import EmailAddress

        EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
        return user, True


def _issue_caja_tickets(order, event, ticket_lines, user, sold_by, mark_as_used):
    """Mint NewTicket rows with the same owner/holder rules as caja v1."""
    user_already_has_ticket = False
    if user:
        user_already_has_ticket = NewTicket.objects.filter(owner=user, event=event).exists()

    tickets_created = []
    for line in ticket_lines:
        ticket_type = line.event_product.ticket_type
        for _ in range(line.quantity):
            ticket_owner = None
            if user and not user_already_has_ticket:
                ticket_owner = user
                user_already_has_ticket = True

            ticket = NewTicket(
                event=event,
                ticket_type=ticket_type,
                order=order,
                holder=user if user else None,
                owner=ticket_owner,
                is_used=mark_as_used,
                used_at=timezone.now() if mark_as_used else None,
                scanned_by=sold_by if mark_as_used else None,
            )
            ticket.save()
            tickets_created.append(ticket)
    return tickets_created


def _send_bonus_issued_email(user, event, order, tickets_created, total_amount):
    from utils.email import send_mail

    send_mail(
        template_name='bonus_issued',
        recipient_list=[user.email],
        context={
            'user': user,
            'event': event,
            'tickets': tickets_created,
            'order': order,
            'total_amount': total_amount,
        },
    )


def _validate_ticket_sale_requirements(ticket_lines, customer_email, mark_as_used):
    if ticket_lines and not mark_as_used and not customer_email:
        raise ValueError('Debe proporcionar un email o marcar como usado (venta en puerta).')


def finalize_caja_sale(caja_sale, net_received_amount=None):
    if caja_sale.status == CajaSale.Status.PAID:
        return caja_sale

    lines = list(caja_sale.lines.select_related('event_product', 'event_product__ticket_type'))
    if not lines:
        raise ValueError('La venta no tiene productos')

    ticket_lines = [line for line in lines if line.event_product.is_ticket_product]
    _validate_ticket_sale_requirements(
        ticket_lines,
        caja_sale.customer_email,
        caja_sale.mark_as_used,
    )

    stock_lines = [{'event_product': line.event_product, 'quantity': line.quantity} for line in lines]

    with transaction.atomic():
        caja_sale = CajaSale.objects.select_for_update().get(pk=caja_sale.pk)
        if caja_sale.status == CajaSale.Status.PAID:
            return caja_sale

        validate_lines(stock_lines)
        deduct_for_lines(
            stock_lines,
            user=caja_sale.sold_by,
            reason=EventProductStockRecord.Reason.SALE,
            caja_sale=caja_sale,
            notes=f'Venta caja {caja_sale.event_caja.name}',
        )

        order = caja_sale.order
        user_created = False

        if ticket_lines:
            customer, user_created = _create_or_get_customer(caja_sale.customer_email)
            event = caja_sale.event_caja.event

            if user_created and customer:
                _send_password_reset_email(customer)

            if not order:
                order = Order.objects.create(
                    first_name=customer.first_name if customer else 'Caja',
                    last_name=customer.last_name if customer else 'Admin',
                    email=caja_sale.customer_email or (customer.email if customer else 'caja@admin.com'),
                    phone=getattr(getattr(customer, 'profile', None), 'phone', '') or '',
                    dni=getattr(getattr(customer, 'profile', None), 'document_number', '') or '',
                    amount=caja_sale.total_amount,
                    event=event,
                    user=customer,
                    status=Order.OrderStatus.CONFIRMED,
                    order_type=_order_type_for_payment(caja_sale.payment_method),
                    generated_by_admin_user=caja_sale.sold_by,
                    net_received_amount=(
                        net_received_amount if net_received_amount is not None else caja_sale.total_amount
                    ),
                )
                caja_sale.order = order
                caja_sale.save(update_fields=['order', 'updated_at'])

                OrderTicket.objects.bulk_create([
                    OrderTicket(
                        order=order,
                        ticket_type=line.event_product.ticket_type,
                        quantity=line.quantity,
                    )
                    for line in ticket_lines
                ])
            else:
                order.user = customer
                order.status = Order.OrderStatus.CONFIRMED
                order.save(update_fields=['user', 'status', 'updated_at'])

            tickets_created = _issue_caja_tickets(
                order,
                event,
                ticket_lines,
                customer,
                caja_sale.sold_by,
                caja_sale.mark_as_used,
            )

            if customer and tickets_created and not caja_sale.mark_as_used:
                try:
                    _send_bonus_issued_email(
                        customer,
                        event,
                        order,
                        tickets_created,
                        caja_sale.total_amount,
                    )
                except Exception as exc:
                    logger.warning('Caja sale %s: bonus email failed: %s', caja_sale.id, exc)
        elif caja_sale.customer_email:
            _create_or_get_customer(caja_sale.customer_email)

        caja_sale.status = CajaSale.Status.PAID
        caja_sale.save(update_fields=['status', 'updated_at'])

    return caja_sale


def create_pending_sale(event_caja, sold_by, payment_method, lines_data, customer_email='', mark_as_used=False):
    if not lines_data:
        raise ValueError('Debe seleccionar al menos un producto')

    stock_lines = []
    total = Decimal('0')
    sale_lines = []
    has_ticket_products = False

    for item in lines_data:
        product = item['event_product']
        quantity = int(item['quantity'])
        if quantity <= 0:
            continue
        if product.is_ticket_product:
            has_ticket_products = True
        unit_price = item.get('unit_price', product.price or Decimal('0'))
        stock_lines.append({'event_product': product, 'quantity': quantity})
        total += unit_price * quantity
        sale_lines.append((product, quantity, unit_price))

    if not sale_lines:
        raise ValueError('Debe seleccionar al menos un producto')

    if payment_method == CajaSale.PaymentMethod.MP_QR:
        from caja.mercadopago_instore import MP_QR_MIN_AMOUNT
        if total < MP_QR_MIN_AMOUNT:
            raise ValueError(
                f'El monto mínimo para cobrar con MP QR es ${MP_QR_MIN_AMOUNT:.0f}',
            )

    if has_ticket_products:
        _validate_ticket_sale_requirements(
            [type('Line', (), {'event_product': p})() for p, _, _ in sale_lines if p.is_ticket_product],
            customer_email,
            mark_as_used,
        )

    validate_lines(stock_lines)

    immediate = payment_method in (
        CajaSale.PaymentMethod.EFECTIVO,
        CajaSale.PaymentMethod.TRANSFERENCIA,
    )

    with transaction.atomic():
        caja_sale = CajaSale.objects.create(
            event_caja=event_caja,
            sold_by=sold_by,
            payment_method=payment_method,
            status=CajaSale.Status.PENDING,
            total_amount=total,
            customer_email=customer_email or '',
            mark_as_used=mark_as_used,
        )
        CajaSaleLine.objects.bulk_create([
            CajaSaleLine(
                caja_sale=caja_sale,
                event_product=product,
                quantity=quantity,
                unit_price=unit_price,
            )
            for product, quantity, unit_price in sale_lines
        ])

    if immediate:
        caja_sale = finalize_caja_sale(caja_sale)

    return caja_sale
