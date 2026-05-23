# Caja v2 (módulo `caja`)

Nueva caja operativa bajo Mi Fuego con productos configurables, stock unificado, múltiples puntos de venta por evento e integración **MercadoPago Instore** (QR dinámico y Postnet/Point). Reemplaza progresivamente la caja Mi Fuego legacy cuando el evento tiene cajas v2 activas.

Para el contexto de roles y la convivencia con `/admin/caja/` y la caja legacy, ver [eventos-roles-y-operacion](eventos-roles-y-operacion.md).

## Modelos principales

| Modelo | Rol |
|--------|-----|
| `EventProduct` | Producto vendible en caja: puede vincularse a un `TicketType` (`show_in_caja=True`) o ser genérico (nombre + precio propios). |
| `EventProductStock` | Stock actual por producto (`quantity`; `null` = ilimitado). |
| `EventProductStockRecord` | Auditoría de movimientos (`INITIAL`, `ADMIN_ADJUST`, `SALE`, `SALE_CANCEL`, `MIGRATION`, `ORDER_MINT`). |
| `EventCaja` | Punto de venta dentro de un evento (nombre, orden, activo). |
| `EventCajaProduct` | Productos habilitados en cada caja (M2M con orden). |
| `EventCajaMercadoPagoConfig` | Store/POS/terminal MP por caja (`qr_ready`, `point_ready`). |
| `CajaSale` | Venta con método de pago, estado (`PENDING` → `PAID` / `CANCELLED` / `EXPIRED`) y enlace opcional a `Order`. |
| `CajaSaleLine` | Línea de venta (producto, cantidad, precio unitario). |

La migración `0002_migrate_ticket_stock` crea un `EventProduct` por cada `TicketType` existente y copia `ticket_count` al stock de caja v2.

## Permisos

| Acción | Quién |
|--------|-------|
| Vender en una caja | `Event.admins` **o** `Event.access_caja` ([`get_event_for_caja`](../caja/permissions.py)) |
| Administrar productos, cajas, stock y reportes | Solo `Event.admins` ([`get_event_for_admin`](../caja/permissions.py)) |

## URLs (prefijo `/mi-fuego/`)

| Ruta | Vista | Descripción |
|------|-------|-------------|
| `cajas-v2/` | Redirige a `caja_events` | Alias legacy |
| `mis-eventos/<slug>/productos/` | `products_list_view` | ABM de productos (admin evento) |
| `mis-eventos/<slug>/productos/<id>/` | `product_edit_view` | Editar producto |
| `mis-eventos/<slug>/productos/<id>/stock/` | `product_stock_view` | Ajustar stock |
| `mis-eventos/<slug>/cajas-v2/` | `cajas_list_view` | Listado de cajas del evento |
| `mis-eventos/<slug>/cajas-v2/<id>/` | `caja_edit_view` | Configurar caja, productos y MP |
| `mis-eventos/<slug>/cajas-v2/<id>/vender/` | `caja_v2_operator_view` | Interfaz de venta |
| `mis-eventos/<slug>/reporte-cajas/` | `caja_sales_report_view` | Reporte de ventas caja v2 |
| `mis-eventos/<slug>/reporte-evento/` | `event_report_view` | Reporte consolidado del evento |
| `mis-eventos/<slug>/reporte-stocks/` | `stock_report_view` | Reporte de stock |
| `…/api/sales/` | `api_create_sale` | Crear venta pendiente (JSON) |
| `…/api/sales/<id>/pay/mp-qr/` | `api_pay_mp_qr` | Iniciar cobro MP QR |
| `…/api/sales/<id>/pay/mp-point/` | `api_pay_mp_point` | Iniciar cobro Postnet |
| `…/api/sales/<id>/status/` | `api_sale_status` | Consultar estado |
| `…/api/sales/<id>/cancel/` | `api_cancel_sale` | Cancelar venta pendiente |

## Flujo de venta

1. El operador elige productos y cantidades en la UI de [`operator.html`](../user_profile/templates/mi_fuego/caja_v2/operator.html).
2. `POST` a la API crea un `CajaSale` en estado `PENDING` con validación de stock.
3. Según el método de pago:
   - **Efectivo / transferencia**: se finaliza al instante (`finalize_caja_sale`).
   - **MP QR / MP Point**: se crea orden MP Instore; al confirmarse el pago (webhook o polling) pasa a `PAID` y se emite bonos / productos vendidos.
4. Si hay productos con bono, se crea `Order` + `NewTicket` y emite email de bonos; si hay tickets, envía PDF por email.
5. Si solo hay productos genéricos, no se crean orden ni bonos.
6. Si hay tickets y no hay email, crea usuario si hace falta (`_create_or_get_customer`).
7. Si `mark_as_used=True`, los bonos quedan como usados al emitir.
8. Email de confirmación de venta caja.

## Métodos de pago (`CajaSale.PaymentMethod`)

| Valor | Etiqueta | `OrderType` resultante |
|-------|---------|-------------------|
| `EFECTIVO` | Efectivo | `CASH_ONSITE` |
| `TRANSFERENCIA` | Transferencia | `LOCAL_TRANSFER` |
| `MP_QR` | Mercado Pago QR | `MP_QR_CAJA` |
| `MP_POINT` | Mercado Pago Postnet | `MP_POINT_CAJA` |

Monto mínimo MP QR: **$15** ([`MP_QR_MIN_AMOUNT`](../caja/mercadopago_instore.py)).

## Stock

- El stock de bonos en checkout web y en caja v2 se consulta vía [`caja.stock`](../caja/stock.py): si existe `EventProduct` para el `TicketType`, usa `EventProductStock`; si no, cae back a `ticket_count`.
- Las ventas descuentan stock; las emisiones online registran `ORDER_MINT`.
- Productos genéricos (sin `ticket_type`) solo existen en caja v2.

## MercadoPago Instore

Configuración automática por evento/caja ([`mercadopago_setup.py`](../caja/services/mercadopago_setup.py)):

- **Store** MP por evento (`external_store_id = EVT{event_id}`).
- **POS** por caja (`external_pos_id = CAJA{caja_id}`) con QR estático de mostrador.
- **Terminal** Postnet: se elige en la edición de caja; una terminal no puede estar en dos cajas activas.

Requiere `MERCADOPAGO_COLLECTOR_USER_ID` (ver [configuracion](configuracion.md)).

Las ventas MP usan `external_reference = caja-sale-{id}`. Los handlers en [`webhook_handlers.py`](../caja/webhook_handlers.py) y [`views/webhooks.py`](../caja/views/webhooks.py) finalizan o cancelan ventas pendientes al recibir confirmación de MP.

## Reportes (solo admin de evento)

- **Reporte cajas** ([`build_caja_sales_report`](../caja/reports.py)): ingresos, ventas por caja/producto/hora/método de pago, stock bajo.
- **Reporte evento** ([`build_event_report`](../caja/reports.py)): consolidado online + caja legacy + caja v2 — bonos emitidos/usados, donaciones, comisiones MP, productos genéricos, ocupación de sede.
- **Reporte stock**: listado de productos con cantidades e ilimitados.

Acceso desde el menú de administración del evento en Mi Fuego ([`event_admin_menu.html`](../user_profile/templates/mi_fuego/partials/event_admin_menu.html)).

## Convivencia con caja legacy

- **`/mi-fuego/mis-eventos/<slug>/caja/`** (GET): si hay **exactamente una** caja v2 activa, redirige a `caja_v2_operator`; si hay varias o ninguna, va al listado de cajas.
- La caja legacy (formulario `CajaEmitirBonoForm`) sigue disponible vía POST en la misma URL para eventos sin caja v2 o como respaldo.
- **`/admin/caja/`** (staff + `can_sell_tickets`) no usa el módulo `caja`; sigue siendo backoffice Django.

## Enlaces en código

| Tema | Archivo |
|------|---------|
| Modelos | [`caja/models.py`](../caja/models.py) |
| Ventas y emisión | [`caja/services/sales.py`](../caja/services/sales.py) |
| Operador | [`caja/views/operator_views.py`](../caja/views/operator_views.py) |
| Admin productos/cajas | [`caja/views/admin_views.py`](../caja/views/admin_views.py) |
| Reportes | [`caja/views/report_views.py`](../caja/views/report_views.py), [`caja/reports.py`](../caja/reports.py) |
| URLs | [`caja/urls.py`](../caja/urls.py) (incluido en [`deprepagos/urls.py`](../deprepagos/urls.py) bajo `mi-fuego/`) |

Ver también [ordenes-y-pagos](ordenes-y-pagos.md), [integraciones](integraciones.md), [glosario](glosario.md).
