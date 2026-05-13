# Evento Fuego Austral (`Event`)

En el código, **evento** casi siempre es una instancia de `Event` en [`events/models.py`](../events/models.py): una edición o instancia de evento de Fuego Austral con su propia home, cupos y reglas. No confundir con “eventos” de analytics o webhooks externos.

## Campos operativos clave

| Campo | Rol |
|-------|-----|
| `active` | Si el evento se considera activo para consultas (`get_active_events`, `get_by_slug`). |
| `is_main` | Un solo evento principal a la vez (constraint `unique_main_event`); es el que se muestra en `/`. |
| `slug` | Identificador en URL; si está vacío se puede derivar del nombre en `clean()`. |
| `name`, `start`, `end` | Identidad y ventana temporal del evento. |
| `max_tickets`, `max_tickets_per_order` | Techo global de bonos vendidos (órdenes confirmadas) y límite por orden. |
| `transfers_enabled_until` | Hasta cuándo se permiten transferencias de bonos (además de reglas en `transfer_period()`). |
| `volunteers_enabled_until`, `has_volunteers` | Ventana y flag de voluntariado. |
| `send_transfer_notifications` | Envío de emails ante transferencias. |
| `ingreso_anticipado_limite_carga` | Límite de carga/edición de ingresos anticipados (null = sin límite). |
| `show_multiple_tickets` | Si es falso, en la home solo se ofrece el tipo de bono más barato disponible (ver manager en tickets). |
| `attendee_must_be_registered` | Si es verdadero, asistentes deben ser usuarios registrados; afecta emails con PDF, etc. |
| `venue_capacity`, `attendees_left` | Capacidad opcional de sede y contador de personas que salieron (ocupación estimada). |
| `header_image`, `title`, `description` | Contenido de la página pública. |
| `location`, `location_url` | Texto y enlace (p. ej. mapa). |

No existe en base de datos un **tipo de evento** (categoría festival/camp/etc.) que cambie la transferencia: la ventana la define el mismo `Event` (`transfers_enabled_until`, `end`). Los **tipos de transferencia de bonos** (usuario con perfil, email sin cuenta, link legado) están detallados en [transferencias-de-bonos](transferencias-de-bonos.md).

## Permisos y roles por evento

- `admins`: usuarios que administran el evento en sentido de negocio (según uso en vistas/admin).
- `access_scanner`: acceso a flujos de scanner del evento.
- `access_caja`: acceso a caja del evento.

Para alta de eventos, matriz de roles, caja Mi Fuego vs `/admin/caja`, bonos dirigidos y **grupos (camps, responsables, ingreso anticipado)**, ver [eventos-roles-y-operacion](eventos-roles-y-operacion.md).

## Permisos Django custom en `Event`

- `view_tickets_sold_report`: ver reporte de bonos vendidos.

## Términos y condiciones

- `EventTermsAndConditions`: textos por evento con `slug` y orden.
- `EventTermsAndConditionsAcceptance`: qué usuario aceptó qué término y en qué orden de compra.

## Grupos (camp, arte, etc.)

- `GrupoTipo`: catálogo (nombre único).
- `Grupo`: por evento, con `lider`, `tipo`, cupos de `ingreso_anticipado_*` y `late_checkout_*`.
- `GrupoMiembro`: usuario en grupo; validación de que tenga bono (`NewTicket`) propio para el evento salvo el líder.

## Métodos útiles

- `tickets_remaining()`: restante global usando órdenes **CONFIRMED** y excluyendo tipos con `ignore_max_amount`.
- `transfer_period()` / `volunteer_period()`: si aún aplica según fechas.
- `venue_occupancy` / `occupancy_percentage`: uso de sede a partir de bonos usados y `attendees_left`.

Para tipos de bono y venta, ver [bonos-y-cupones](bonos-y-cupones.md). Para alta operativa de un evento nuevo, el README enlaza un [Google Doc](https://docs.google.com/document/d/1_8NBQMMYZ68ABRQs2Fy-BX296OZnTdzzGWp6yNr_KEU/edit) de proceso editorial/diseño.
