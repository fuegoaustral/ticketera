# Transferencias de bonos

En el código conviven el bono **actual** (`NewTicket`) y el modelo **histórico** (`Ticket`). Las formas de “transferir” no son un solo flujo: importa si el destinatario **ya tiene cuenta con perfil completo**, si el bono salió de **emisión directa a un email sin cuenta**, o si se usa el **flujo legado por enlace**.

## Aclaración: “tipos de eventos” vs tipos de transferencia

- **Evento FA** (`Event`): no hay un enum en base de datos de “tipo de evento” (festival, regional, etc.). Las reglas de transferencia dependen del **mismo** evento: `transfers_enabled_until`, `end`, y `send_transfer_notifications` (emails recordatorios en [`tickets/email_crons.py`](../tickets/email_crons.py)).
- Lo que sí hay son **distintos tipos de transferencia de bonos** (esta página).

---

## 1. Transferencia a usuario con cuenta y datos completos (`NewTicket`)

**Quién:** el **holder** del bono, logueado en Mi Fuego.  
**Dónde:** pantalla de bonos transferibles y modal “Transferir”; API `POST` a la ruta nombrada `transfer_ticket` ([`tickets/views/new_ticket.py`](../tickets/views/new_ticket.py), URL tipo `/ticket/<ticket_key>/transfer/` en [`tickets/urls.py`](../tickets/urls.py)).

**Requisitos del destinatario:**

- Debe existir un `User` cuyo **email** coincida (normalizado en minúsculas).
- El perfil debe estar en **`profile_completion == 'COMPLETE'`**. Si el email no existe o el perfil no está completo, la API responde error con código **`EMAIL_NOT_FOUND`** y mensaje pidiendo verificar que el destinatario tenga cuenta activa en Fuego Austral.

**Comportamiento:** traspaso **inmediato** de `holder` (y lógica de `owner` según si el destinatario ya tenía otro bono como owner). Se limpian flags de voluntariado en el bono. Se registra `NewTicketTransfer` (en el código actual el estado guardado es `COMPLETED`, distinto de las etiquetas canónicas del modelo `PENDING` / `CONFIRMED` / `CANCELLED`; conviene alinear en una futura corrección). Se envía email de éxito al destinatario.

**Restricciones:**

- No se puede transferir a un email asociado a la propia cuenta (todos los `EmailAddress` del usuario).
- Debe seguir vigente el periodo de transferencias del evento (`transfer_period()`).
- No puede haber otra transferencia **PENDING** para el mismo bono (validación en vista).
- Solo el holder puede iniciar la operación.

En la UI, el botón “Transferencia completa” en [`transferable_tickets.html`](../user_profile/templates/mi_fuego/my_tickets/transferable_tickets.html) refleja este flujo cuando el destinatario ya está “listo” en términos de cuenta.

---

## 2. “Transferencia” por email sin cuenta todavía (emisión directa / bonos dirigidos)

Cuando la **emisión directa** crea bonos para un email **sin** usuario existente ([`direct_sales_new_user`](../utils/direct_sales.py)):

- Por cada bono se crea un `NewTicketTransfer` en estado **`PENDING`** con `tx_to_email` y `tx_from` el operador que emitió (comentario en código: debería ser sistema).
- Se envía mail **`new_transfer_no_account`** con link de alta (`account_signup` + query `email`).

El destinatario **necesita registrarse y completar datos** para cerrar el circuito operativo que el producto espera; mientras tanto los recordatorios a receptor/emisor pueden correr por cron si el evento tiene **`send_transfer_notifications`** y la ventana de transferencias sigue abierta ([`tickets/email_crons.py`](../tickets/email_crons.py)).

Esto no es exactamente “el mismo botón Transferir de Mi Fuego”, pero es el caso **transferencia porque hace falta un usuario con datos** en el sentido de negocio.

---

## 3. Transferencia “simple por link” (modelo legado `Ticket` + `TicketTransfer`)

Pensado para el bono **viejo** `Ticket` (no `NewTicket`):

| Paso | Qué pasa |
|------|----------|
| Formulario | `GET/POST` [`ticket_transfer`](../tickets/views/ticket.py): quien transfiere completa **nombre, apellido, email, teléfono y DNI** del destinatario (`TransferForm` / `TicketPerson`). No hace falta que el destinatario tenga cuenta. |
| Email | Se notifica al **email actual del bono** (`transfer.send_email`) con contexto del traspaso. |
| Confirmación | Vista de “esperando confirmación” del destinatario. |
| Cierre | `GET` `ticket/<transfer_key>/confirmed` → `TicketTransfer.transfer()`: copia datos al `Ticket`, marca transferido, reenvía email del bono. |

Rutas en [`tickets/urls.py`](../tickets/urls.py): `ticket/<ticket_key>/transfer`, `.../confirmation`, `ticket/<transfer_key>/confirmed`.

**Importante:** el detalle público QR actual usa **`NewTicket`** ([`public_ticket_detail`](../tickets/views/ticket.py)). El flujo por link legacy aplica a ventas antiguas que aún tengan `Ticket`; no mezclar URLs de un sistema con el otro.

---

## Tabla resumen

| Aspecto | NewTicket → usuario registrado | NewTicket → email sin cuenta (directo) | Ticket + TicketTransfer (link) |
|---------|-------------------------------|----------------------------------------|----------------------------------|
| Modelo de bono | `NewTicket` | `NewTicket` | `Ticket` |
| Cuenta destino al inicio | Sí, perfil completo | No | No |
| Datos del destinatario | Ya en perfil | Tras signup | Formulario quien transfiere |
| URLs típicas | `POST .../ticket/<uuid>/transfer/` | Emisión admin + email | `.../ticket/<key>/transfer` … `confirmed` |

---

## Otras operaciones relacionadas (no son “transferencia” al mismo titular)

- **Asignar** (`assign_ticket`): el holder sin `owner` vincula el bono a sí mismo (GET); permitido aunque haya cerrado el periodo de transferencias para desvinculados ([comentario en código](../tickets/views/new_ticket.py)).
- **Desvincular** (`unassign_ticket`): quita `owner` dentro del periodo; puede borrar membresías de grupo con ingreso anticipado / late checkout no líder.

Ver también [evento-fa](evento-fa.md) (fechas y `transfers_enabled_until`), [reglas-de-negocio](reglas-de-negocio.md) y [ordenes-y-pagos](ordenes-y-pagos.md).
