# Reglas de negocio

Lista de comportamientos codificados que suelen consultar operaciones o QA. Siempre verificar en el código citado tras un cambio.

## Evento principal y visibilidad

- Solo un `Event` con `is_main=True` a la vez (constraint + validación en `Event.clean()`).
- La raíz `/` usa `Event.get_main_event()` (activo y principal).
- Eventos por slug: solo `active=True` en `get_by_slug`.

## Cupo global del evento

- `Event.tickets_remaining()` usa órdenes **CONFIRMED** y suma cantidades de `OrderTicket`, **excluyendo** tipos con `ignore_max_amount=True`.
- Si `max_tickets` es null, el método devuelve un número muy alto (sin techo práctico en esa función).

## Visibilidad y elección de tipos de bono

- Tipos `is_direct_type=True` no entran en listados públicos estándar de “próximos” / “actuales” para la home pública.
- Sin cupón: no se ofrecen tipos cuyo `price` sea null o solo cupón (filtro `price__isnull=False`, `price__gte=0` en `get_available`).
- Con cupón agotado (`tickets_remaining <= 0`): no hay tipos disponibles.
- `show_multiple_tickets=False`: solo el tipo más barato disponible (una fila).
- Ventanas `date_from` / `date_to` y `ticket_count` (stock) filtran en todos los managers relevantes.

## Órdenes y emisión

- El mint solo arranca al entrar en `PROCESSING` desde otro estado en el mismo `save()` (comparación con `_old_status`).
- `mint_tickets` pone la orden en `CONFIRMED` al terminar; si falla, revisar logs (transacción atómica por bloque de creación).

## Stock por tipo

- Cada `NewTicket.save()` en creación decrementa `ticket_count` del `TicketType` (cuidado con dobles saves o tests).

## Transferencias y voluntarios

- `Event.transfer_period()` exige `end` futuro y `transfers_enabled_until` no vencido.
- `volunteer_period()` análogo con `volunteers_enabled_until` y `has_volunteers`.
- Flujos de traspaso (`NewTicket` a usuario con perfil completo, emisión a email con `PENDING`, modelo legado `TicketTransfer` por enlace): [transferencias-de-bonos](transferencias-de-bonos.md).

## Perfil y asistentes

- `attendee_must_be_registered` en el evento condiciona políticas de asignación y emails (PDF a no registrados en ciertos flujos).

## Grupos

- `GrupoMiembro.clean`: el usuario debe tener un `NewTicket` con `holder` y `owner` iguales a sí mismo para el `event` del grupo, salvo el líder (se agrega automáticamente al crear el grupo).

## Permisos Django

- `Order` meta: `can_sell_tickets` para venta en caja.
- `Event` meta: `view_tickets_sold_report` para reportes de ventas.

## Middleware

- `ProfileCompletionMiddleware` y `DeviceDetectionMiddleware` ([`tickets/middleware.py`](../tickets/middleware.py)) pueden redirigir o enriquecer contexto según perfil incompleto o dispositivo.

Para integraciones que disparan cambios de estado, ver [integraciones](integraciones.md) y [ordenes-y-pagos](ordenes-y-pagos.md).
