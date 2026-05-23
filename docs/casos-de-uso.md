# Casos de uso

Actores principales y superficies de la aplicación. Las rutas siguen [`deprepagos/urls.py`](../deprepagos/urls.py) y [`tickets/urls.py`](../tickets/urls.py).

## Comprador (web pública)

- Entra al sitio en `/` (evento **principal** `is_main`) o en `/<slug-del-evento>/` para un evento activo.
- Ve tipos de bono disponibles según fechas, stock y reglas del evento (incl. cupón en query string si aplica).
- Inicia compra desde `new-order/<ticket_type_id>/`, completa datos y puede donar (becas arte, sede, inclusión).
- Paga con MercadoPago (preferencia de pago) o confirma orden gratuita según flujo.
- Recibe confirmación por email; según configuración del evento puede recibir PDFs de bonos adjuntos.

## Titular o asistente del bono

- Gestiona bonos desde **Mi Fuego** (`/mi-fuego/…`, perfiles y URLs definidas en [`user_profile/urls.py`](../user_profile/urls.py)).
- Asigna o desasigna titular, transfiere bonos (mientras el evento permita periodo de transferencia), ve QR en vista pública del bono (`/bono/<ticket_key>/` o con prefijo de evento). Tipos de transferencia (a usuario registrado vs otros flujos): [transferencias-de-bonos](transferencias-de-bonos.md).
- Ve y desbloquea **logros** en `/mi-fuego/mis-bonos/logros/`; modal de celebración tras compras online confirmadas. Ver [logros](logros.md).

## Staff de caja

Hay **tres** superficies (detalle en [eventos-roles-y-operacion](eventos-roles-y-operacion.md) y [caja-v2](caja-v2.md)):

1. **`/admin/caja/`** — usuario **staff** Django + permiso **`tickets.can_sell_tickets`**: caja backoffice con selector de evento y todos los `TicketType` del evento.
2. **`/mi-fuego/cajas/`** y **`/mi-fuego/mis-eventos/<slug>/caja/`** — caja legacy Mi Fuego; redirige a v2 si hay una sola caja activa.
3. **`/mi-fuego/mis-eventos/<slug>/cajas-v2/<id>/vender/`** — caja v2 con productos, stock, MP QR/Postnet y reportes (admin de evento).

## Administrador de evento (reportes caja v2)

- **`/mi-fuego/mis-eventos/<slug>/reporte-cajas/`** — ventas por caja v2.
- **`/mi-fuego/mis-eventos/<slug>/reporte-evento/`** — consolidado online + caja (bonos, donaciones, comisiones MP, productos genéricos).
- **`/mi-fuego/mis-eventos/<slug>/reporte-stocks/`** — stock de productos.

## Staff de ingreso / scanner

- Accede a `/scan/` o `/scan/<event_slug>/` y al dashboard del scanner cuando corresponde.
- APIs bajo `/api/tickets/…` y `/api/events/…` para marcar uso, salida/reingreso, notas y estadísticas (ver código en [`tickets/views/admin.py`](../tickets/views/admin.py)).

## Administrador Django

- Panel estándar en `/admin/` para eventos, tipos de bono, órdenes, cupones, usuarios, etc.
- Flujos especiales: **venta directa / bonos dirigidos** (`/admin/direct_tickets/…`, staff + `can_sell_tickets`), **caja admin** (`/admin/caja/`).

## Espacio Zen (reservas)

- Módulo separado bajo `/espaciozen/` con APIs de disponibilidad, creación, listado, edición y borrado de reservas ([`espaciozen/urls.py`](../espaciozen/urls.py)). Integra calendario vía credenciales de servicio (ver [integraciones](integraciones.md)).

## Webhooks y sistemas externos

- MercadoPago notifica pagos en `POST /webhooks/mercadopago` ([`tickets/views/webhooks.py`](../tickets/views/webhooks.py)), con verificación HMAC.

## Referencia rápida de URLs (tickets)

| Área | Patrón (ejemplos) |
|------|-------------------|
| Home / evento | `/`, `/<slug>/`, `/eventos/` |
| Checkout multi-paso | `/checkout/select-tickets`, `select-donations`, `order-summary` |
| Orden | `/order/<order_key>`, callbacks de pago |
| Bono público | `/bono/<ticket_key>/` |
| Transferencias | `/ticket/<ticket_key>/transfer`, confirmaciones |
| Scanner | `/scan/`, `/scan/<slug>/` |
| Caja v2 (Mi Fuego) | `/mi-fuego/mis-eventos/<slug>/cajas-v2/<id>/vender/` |
| Reportes evento (admin) | `/mi-fuego/mis-eventos/<slug>/reporte-evento/` |
| Logros | `/mi-fuego/mis-bonos/logros/` |

Para detalle de reglas (cupos, cupones, estados), ver [reglas-de-negocio](reglas-de-negocio.md) y [ordenes-y-pagos](ordenes-y-pagos.md).
