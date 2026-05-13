# Creación de eventos, roles y operación (caja, admin, bonos dirigidos, grupos)

Guía operativa alineada con el código. Para el checklist editorial de un evento nuevo (comunicación, diseño), el README enlaza también un [Google Doc de proceso](https://docs.google.com/document/d/1_8NBQMMYZ68ABRQs2Fy-BX296OZnTdzzGWp6yNr_KEU/edit).

## Cómo crear un evento (administrador Django)

1. Ingresá a **`/admin/`** con un usuario **staff** con permisos sobre el modelo `Event` (típicamente superusuario o grupo de administración).
2. En **Eventos → Event → Agregar** completá al menos:
   - **Nombre**, **inicio** y **fin** del evento FA.
   - **`transfers_enabled_until`** (obligatorio en modelo): hasta cuándo se permiten transferencias de bonos.
   - **`active`**: marcarlo cuando el evento deba aparecer en listados y URLs públicas.
   - **`slug`**: recomendado para URLs estables (`/<slug>/`); si queda vacío, en el guardado puede generarse desde el nombre.
   - **`is_main`**: solo un evento puede ser principal; define qué se muestra en `/`.
3. Cargá **contenido de home** (`header_image`, `title`, `description`), **ubicación** y límites (`max_tickets`, `max_tickets_per_order`, etc.).
4. Asigná **roles del evento** (ver siguiente sección): `admins`, `access_scanner`, `access_caja` con el widget de selección múltiple del admin ([`events/admin.py`](../events/admin.py)).
5. Creá **tipos de bono** (`TicketType`) para ese evento: precios, stock inicial en `ticket_count`, ventanas `date_from` / `date_to`, `show_in_caja`, `is_direct_type` solo donde corresponda (emisión directa / bonos dirigidos).

Sin permisos de admin Django, no se puede dar de alta el evento; el equipo de operaciones suele pedir esto a quien tenga acceso staff.

## Roles y permisos (resumen)

| Rol / permiso | Dónde se configura | Qué habilita en la app |
|---------------|-------------------|-------------------------|
| **Staff + permisos de modelo** | Usuario en `/admin/` (grupos o permisos sueltos) | Entrar al admin Django, editar eventos, órdenes, importar bonos dirigidos, etc. |
| **`tickets.can_sell_tickets`** | Permiso del modelo `Order` (“Can sell tickets in Caja”) | Vistas **`/admin/caja/`** y **`/admin/direct_tickets/…`** ([`tickets/admin.py`](../tickets/admin.py)): requiere además `@staff_member_required`. |
| **Administrador de evento** (`Event.admins`) | Admin del `Event` → campo `admins` | Scanner de ese evento ([`has_scanner_access`](../tickets/views/admin.py)), listado **Cajas** en Mi Fuego, caja Mi Fuego del evento, gestión de usuarios de caja en vistas de perfil. |
| **Acceso scanner** (`Event.access_scanner`) | Admin del `Event` | Scanner y APIs de check-in para bonos de ese evento (junto con admins y superusuarios). |
| **Acceso caja (Mi Fuego)** (`Event.access_caja`) | Admin del `Event` o flujos en Mi Fuego que agregan/quitan usuarios | Aparece en **`/mi-fuego/cajas/`** y puede abrir **`/mi-fuego/mis-eventos/<slug>/caja/`** ([`caja_view`](../user_profile/views.py)). |
| **Superusuario** | `createsuperuser` / admin | Acceso total; en scanner se considera siempre autorizado. |
| **Grupo Django `Puerta`** (legado) | Grupos de usuario | Si no hay `event` en contexto, `has_scanner_access` puede permitir scanner por compatibilidad ([`tickets/views/admin.py`](../tickets/views/admin.py)). |

### Dos flujos de “caja” (no son lo mismo)

1. **Caja staff (backoffice)** — URL **`/admin/caja/`**  
   - Requiere: usuario **staff** + permiso **`can_sell_tickets`**.  
   - Lista **todos** los eventos en el selector y armó el formulario con **todos** los `TicketType` del evento ([`TicketPurchaseForm`](../tickets/forms.py)).  
   - Crea usuario si hace falta, orden **CONFIRMED**, `generated_by_admin_user`, líneas `OrderTicket` y llama a **`mint_tickets`**.

2. **Caja Mi Fuego (operadores del evento)** — **`/mi-fuego/cajas/`** → **`/mi-fuego/mis-eventos/<slug>/caja/`**  
   - Requiere: usuario logueado y ser **`admins`** del evento **o** estar en **`access_caja`** para ese evento.  
   - Solo tipos con **`show_in_caja=True`** ([`CajaEmitirBonoForm`](../user_profile/forms.py)); respeta reglas de emisión y opción “marcar como usada (venta en puerta)” en la vista.

Un operador puede tener **acces_caja** sin ser staff: usa la caja de Mi Fuego, no `/admin/caja/`.

## Administrador de evento (`admins`)

Los **admins** del `Event` son el “dueño operativo” del evento en la aplicación:

- Pueden usar el **scanner** del evento aunque no estén en `access_scanner`.
- Aparecen en la unión de eventos para **Cajas** en Mi Fuego (`admins` ∪ `access_caja`).
- Pueden gestionar quién tiene caja en las pantallas de configuración de caja bajo Mi Fuego (vistas que agregan/quitan `access_caja` en [`user_profile/views.py`](../user_profile/views.py)).

No confundir con **superusuario Django**: un admin de evento puede ser un usuario normal sin staff.

## Admin Django (`/admin/`)

- Gestión CRUD de **eventos**, **tipos de bono**, **cupones**, **órdenes**, **bonos emitidos**, **términos y condiciones**, **grupos**, import/export de **bonos dirigidos**, etc.
- Cabecera del sitio admin personalizada: “Bonos de Fuego Austral” ([`tickets/admin.py`](../tickets/admin.py)).
- Enlace a **caja** y **venta directa** en la UI del admin si el usuario tiene `can_sell_tickets` ([`tickets/templates/admin/base_site.html`](../tickets/templates/admin/base_site.html)).

## Bonos dirigidos y emisión directa (FA, camps, arte, organización)

### Concepto

Los **bonos dirigidos** son plantillas (`DirectTicketTemplate`, verbose “Bono dirigido”) que reservan cupos para asignar a personas concretas (camps, voluntariado, arte u organización), sin pasar por la compra web estándar.

- **`origin`**: `CAMP`, `VOLUNTARIOS` (etiqueta “Voluntarios”), `ARTE`, `ORGANIZACION` ([`DirectTicketTemplateOriginChoices`](../tickets/models.py)).
- **`name`**, **`amount`** (cupos totales), **`email`** opcional, **`event`**, **`status`** (`AVAILABLE`, `PENDING`, `ASSIGNED`), **`amount_used`**, enlace opcional a **`order`**.

Se administran en **`/admin/tickets/directtickettemplate/`** (import/export CSV vía `django-import-export` en [`DirectTicketTemplateAdmin`](../tickets/admin.py)). El modelo define también el permiso Django **`admin_volunteers`** (“Can admin Volunteers”) para extensiones de permisos sobre voluntariado.

### Tipo de bono técnico (`is_direct_type`)

Para cada evento que use emisión directa debe existir al menos un **`TicketType`** con **`is_direct_type=True`** para ese evento. Ese tipo:

- No se mezcla con la venta pública habitual de la home (`is_direct_type=False` en managers).
- Es el que usa **`direct_sales_existing_user` / `direct_sales_new_user`** al mintear bonos ([`utils/direct_sales.py`](../utils/direct_sales.py)): precio total de la orden se recalcula según cantidad emitida × precio del tipo directo.

### UI de redención

Usuarios **staff** con **`can_sell_tickets`** acceden a **`/admin/direct_tickets/`** (y subrutas buyer/congrats):

- Eligen evento activo, ven plantillas de bonos dirigidos del evento y cantidad disponible (`amount - amount_used`).
- Al redimir, se crea orden **CONFIRMED**, bonos `NewTicket`, se actualiza plantilla y se notifica por email según flujo.

## Grupos (camps), ingreso anticipado y responsables

### Modelos ([`events/models.py`](../events/models.py))

- **`GrupoTipo`**: catálogo de tipos (ej. nombres tipo camp, arte, caos); **`activo`** para filtrar.
- **`Grupo`**: pertenece a un **`event`**, tiene **`nombre`**, **`tipo`**, **`lider`** (**responsable** del grupo), y cupos:
  - **`ingreso_anticipado_amount`**, **`ingreso_anticipado_desde`**: cuántas personas pueden tener ingreso anticipado y desde cuándo aplica.
  - **`late_checkout_amount`**, **`late_checkout_hasta`**: cupo y ventana de late checkout.
- **`GrupoMiembro`**: vincula **`user`** al **`grupo`** con flags **`ingreso_anticipado`**, **`ingreso_anticipado_fecha`**, **`late_checkout`**, **`restriccion`** (alimentaria u otras opciones).

Al **crear** un `Grupo`, una señal agrega automáticamente al **responsable** (`lider`) como miembro del grupo.

### Regla de bonos para miembros

Al agregar un miembro que no es el líder, **`GrupoMiembro.clean`** exige que el usuario tenga un **`NewTicket`** con **`holder`** y **`owner`** iguales a sí mismo para el **mismo evento** que el grupo. Así solo entran al listado quienes ya tienen bono a su nombre.

### Ingreso anticipado a nivel evento

El campo del **`Event`**: **`ingreso_anticipado_limite_carga`** limita hasta cuándo se pueden cargar o modificar datos de ingreso anticipado a nivel global del evento (null = sin límite). Complementa las ventanas por grupo (`ingreso_anticipado_desde`, inicio del evento en validaciones de negocio donde existan).

## Enlaces útiles en código

| Tema | Archivo |
|------|---------|
| Formulario admin caja | [`tickets/admin.py`](../tickets/admin.py) (`admin_caja_view`) |
| Formulario Mi Fuego caja | [`user_profile/views.py`](../user_profile/views.py) (`caja_view`), [`user_profile/forms.py`](../user_profile/forms.py) (`CajaEmitirBonoForm`) |
| Lista de eventos con caja | [`user_profile/views.py`](../user_profile/views.py) (`caja_events_view`) |
| Scanner / permisos | [`tickets/views/admin.py`](../tickets/views/admin.py) (`has_scanner_access`, `scan_tickets_event`) |
| Emisión directa | [`utils/direct_sales.py`](../utils/direct_sales.py), [`tickets/admin.py`](../tickets/admin.py) (`admin_direct_tickets_*`) |
| Modelo evento y grupos | [`events/models.py`](../events/models.py) |

Ver también [transferencias-de-bonos](transferencias-de-bonos.md), [evento-fa](evento-fa.md), [bonos-y-cupones](bonos-y-cupones.md) y [casos-de-uso](casos-de-uso.md).
