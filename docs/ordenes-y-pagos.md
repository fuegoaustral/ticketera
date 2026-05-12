# Órdenes y pagos

Modelo principal: `Order` en [`tickets/models.py`](../tickets/models.py). Las líneas son `OrderTicket` (tipo + cantidad). La emisión de bonos físicos en base de datos es `NewTicket` vía [`tickets/processing.py`](../tickets/processing.py).

## Estados (`OrderStatus`)

| Valor | Significado típico |
|-------|---------------------|
| `PENDING` | Creada, esperando pago o confirmación. |
| `PROCESSING` | Pago recibido / en proceso de acreditación; dispara **mint** de bonos. |
| `CONFIRMED` | Orden cerrada OK tras `mint_tickets`. |
| `ERROR` | Fallo de negocio o integración. |
| `REFUNDED` | Reembolsada. |

## Tipos de orden (`OrderType`)

Incluye `ONLINE_PURCHASE`, transferencias internacional/local, `CASH_ONSITE`, `OTHER`. El default es compra online.

## Emisión de bonos (`mint_tickets`)

1. Al guardar una `Order`, si el estado pasa a `PROCESSING` y antes no lo estaba, se llama `mint_tickets(order)` desde `Order.save()`.
2. `mint_tickets` crea un `NewTicket` por cada unidad en cada `OrderTicket` del mismo evento, en transacción.
3. Reglas de `owner`: si el usuario aún no tenía bono y la orden no mezcla varios tipos, el primer bono toma `owner = order.user`.
4. Al finalizar el mint, la orden pasa a `CONFIRMED` y se envía email de confirmación (`send_confirmation_email`), con PDFs adjuntos si aplica según evento y dependencias.

## MercadoPago

### Preferencia de pago

`Order.get_payment_preference()` arma ítems (bonos + donaciones), `back_urls` con `APP_URL` y crea la preferencia con el SDK. El `external_reference` en ese método histórico usa el **id entero** de la orden; otros flujos pueden usar la **key** UUID (ver checkout).

### Webhook

[`tickets/views/webhooks.py`](../tickets/views/webhooks.py):

- `POST /webhooks/mercadopago` (CSRF exempt).
- Verifica firma HMAC con `MERCADOPAGO_WEBHOOK_SECRET`, cabeceras `x-signature` y `x-request-id`.
- Para `action == payment.created`, obtiene el pago en la API; si `status == approved`, llama `order_approved`.
- `order_approved` busca la orden y, si sigue `PENDING`, la pone en `PROCESSING`, guarda callback y `net_received_amount`, y `save()` dispara el mint.

**Nota:** conviene que `external_reference` en el pago coincida con lo que espera cada ruta (`Order.objects.get(key=…)` en webhook vs `id` en preferencia legacy). El flujo de checkout moderno en [`tickets/views/checkout.py`](../tickets/views/checkout.py) usa `str(order.key)` como `external_reference`.

### Otros callbacks

Rutas bajo `order/<order_key>/payments/success|failure|pending` y `payments/ipn/` según [`tickets/urls.py`](../tickets/urls.py). Hay cron de reconciliación en [`tickets/payment_check_cron.py`](../tickets/payment_check_cron.py) para órdenes pendientes.

## Campos útiles de auditoría

- `processor_callback`: payload del procesador.
- `generated_by_admin_user`: orden creada desde admin/caja.
- `coupon`: cupón aplicado si existe.

Para reglas de cupos y visibilidad de tipos, ver [reglas-de-negocio](reglas-de-negocio.md).
