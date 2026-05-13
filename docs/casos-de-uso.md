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

## Staff de caja

Hay **dos** superficies (detalle en [eventos-roles-y-operacion](eventos-roles-y-operacion.md)):

1. **`/admin/caja/`** — usuario **staff** Django + permiso **`tickets.can_sell_tickets`**: caja backoffice con selector de evento y todos los `TicketType` del evento.
2. **`/mi-fuego/cajas/`** y **`/mi-fuego/mis-eventos/<slug>/caja/`** — usuario en **`Event.admins`** o **`Event.access_caja`**: caja operativa con tipos **`show_in_caja=True`**.

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

Para detalle de reglas (cupos, cupones, estados), ver [reglas-de-negocio](reglas-de-negocio.md) y [ordenes-y-pagos](ordenes-y-pagos.md).
