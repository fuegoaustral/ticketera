# Bonos (tipos de bono) y cupones

Modelos en [`tickets/models.py`](../tickets/models.py): `TicketType`, `Coupon`, líneas de pedido `OrderTicket`, y bonos emitidos `NewTicket`.

## `TicketType`

Representa una **tarifa** o categoría de bono para un `Event`.

| Campo | Rol |
|-------|-----|
| `event` | Evento FA al que pertenece. |
| `price`, `price_with_coupon` | Precio base y precio cuando aplica cupón vinculado. |
| `date_from`, `date_to` | Ventana de venta (null = sin límite en ese extremo). |
| `ticket_count` | Stock **restante** lógico: al emitir un `NewTicket`, el `save()` del bono decrementa este contador. |
| `name`, `description`, `color`, `emoji` | Presentación en UI y emails. |
| `cardinality` | Orden opcional entre tipos. |
| `is_direct_type` | Tipos para **emisión directa** (venta admin / bonos dirigidos); **excluidos** de listados públicos habituales (`get_available_ticket_types_for_current_events`, home). Debe existir uno por evento que use redención de plantillas `DirectTicketTemplate` ([`utils/direct_sales.py`](../utils/direct_sales.py)). |
| `show_in_caja` | Si aparece en flujos de caja (legacy y v2). En caja v2 debe estar activo para vincular un `EventProduct`. |
| `ignore_max_amount` | No cuenta contra `Event.max_tickets` al agregar órdenes confirmadas. |
| `volunteer_price` | Marca de precio especial voluntarios. |

## `Coupon`

- `token`: string corto (p. ej. en query `?coupon=`).
- `max_tickets`: tope de bonos vendidos con ese cupón (órdenes confirmadas que referencian el cupón).
- `ticket_type`: el tipo de bono asociado a esa campaña.

`Coupon.tickets_remaining()` cuenta ventas confirmadas ligadas al cupón.

## Managers y consultas

- `get_available_ticket_types_for_current_events()`: eventos activos, ventana de fechas, stock > 0 o null, no direct types, orden por `cardinality`, `price`.
- `get_next_ticket_type_available(event)`: siguiente tipo futuro (para mensajes “próxima venta”).
- `get_available(coupon, event)`: conjunto que puede comprarse **ahora**:
  - respeta `event.tickets_remaining() > 0`;
  - ventana `date_from` / `date_to` y stock;
  - con cupón: filtra por `coupon` y cupón con stock;
  - sin cupón: excluye tipos solo-cupón (`price` no nulo y ≥ 0);
  - si `event.show_multiple_tickets` es falso, deja solo el más barato (`price` o `price_with_coupon` según cupón).

## `NewTicket` (bono emitido)

- `key`: UUID público en URLs y QR.
- `order`, `event`, `ticket_type`: procedencia.
- `owner` / `holder`: dueño y portador (pueden diferir tras asignación/transferencia).
- `is_used`, `used_at`, `scanned_by`: ingreso.
- `holder_left`, `left_at`: salida temporal de la sede.
- Flags de voluntariado (`volunteer_ranger`, etc.) según operación del evento.

Al crearse un `NewTicket`, el `save()` descuenta `ticket_count` del `TicketType` en una transacción.

### Stock unificado (caja v2)

Con el módulo `caja`, cada `TicketType` puede tener un `EventProduct` asociado cuyo `EventProductStock.quantity` es la fuente de verdad para disponibilidad en checkout web y caja v2 ([`caja/stock.py`](../caja/stock.py)). La migración inicial copia `ticket_count` a ese stock. Ver [caja-v2](caja-v2.md).

Ver también [ordenes-y-pagos](ordenes-y-pagos.md) para cuándo se crean estos registros y [reglas-de-negocio](reglas-de-negocio.md) para límites globales.

## Bonos dirigidos (`DirectTicketTemplate`)

Plantillas con origen **Camp / Voluntarios / Arte / Organización**, cantidad y estado; se importan o editan en el admin y se redimen desde **`/admin/direct_tickets/`** (permiso `can_sell_tickets`). Ver [eventos-roles-y-operacion](eventos-roles-y-operacion.md).
