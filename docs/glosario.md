# Glosario

| Término | Significado en Ticketera |
|---------|---------------------------|
| **Evento / Evento FA** | Instancia de `Event`: una instancia de evento de Fuego Austral con URL, fechas y reglas propias. |
| **Tipo de bono** | `TicketType`: tarifa/categoría con precio, stock y ventana de venta. |
| **Bono** | `NewTicket`: derecho de ingreso emitido para una persona/evento/tipo; tiene `key` UUID y QR. |
| **Orden** | `Order`: compra o registro de pago que agrupa líneas `OrderTicket` y puede generar varios `NewTicket`. |
| **Cupón** | `Coupon`: código/token que acota un tipo de bono y un máximo de usos. |
| **Principal** | Evento con `is_main=True` mostrado en `/`. |
| **Administrador de evento** | Usuario en `Event.admins`: scanner, caja Mi Fuego y gestión de operadores de caja para ese evento. |
| **Caja (admin)** | `/admin/caja/`: staff + permiso `can_sell_tickets`; todos los `TicketType` del evento. |
| **Caja (Mi Fuego)** | `/mi-fuego/mis-eventos/<slug>/caja/`: solo si sos `admins` o `access_caja` del evento; tipos con `show_in_caja`. |
| **Bono dirigido** | Plantilla `DirectTicketTemplate` (camp, arte, etc.) con cupos para emisión directa. |
| **Responsable de grupo** | Usuario `lider` de un `Grupo` (camp/tipo); entra automático como miembro. |
| **Scanner** | Interfaz y APIs bajo `/scan/` para validar y marcar uso de bonos. |
| **Venta directa / bonos dirigidos** | UI `/admin/direct_tickets/` (staff + `can_sell_tickets`); usa `DirectTicketTemplate` y `TicketType` con `is_direct_type`. |
| **Transferencia (NewTicket)** | A otro usuario con cuenta y perfil `COMPLETE`; `POST` `/ticket/<key>/transfer/`. |
| **Transferencia pendiente por email** | Tras emisión directa sin cuenta; `NewTicketTransfer` `PENDING` + mail de alta. |
| **Transferencia por link (legado)** | `Ticket` + `TicketTransfer`: formulario con datos del destinatario y confirmación por URL. |
| **Mint** | Creación de registros `NewTicket` al confirmar pago (`mint_tickets`). |
| **Mi Fuego** | Área de cuenta/perfil bajo `/mi-fuego/` (`allauth` + `user_profile`). |
| **Espacio Zen** | Módulo de reservas de espacio/calendario bajo `/espaciozen/`. |
| **Ingreso anticipado** | Flags y cupos en `Grupo` / `GrupoMiembro` para entrada anticipada o late checkout. |

Enlaces útiles: [transferencias-de-bonos](transferencias-de-bonos.md), [eventos-roles-y-operacion](eventos-roles-y-operacion.md), [evento-fa](evento-fa.md), [bonos-y-cupones](bonos-y-cupones.md), [ordenes-y-pagos](ordenes-y-pagos.md).
