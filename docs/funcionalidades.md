# Funcionalidades por módulo

Resumen de apps Django en `INSTALLED_APPS` y responsabilidades. El código vive en el repo bajo cada carpeta homónima.

## `tickets`

- Modelos de negocio: `TicketType`, `Coupon`, `Order`, `OrderTicket`, `NewTicket`, transferencias asociadas, fotos de uso.
- Vistas públicas: home por evento, checkout, orden, callbacks MercadoPago, vista pública de bono, transferencias.
- Operación en campo: scanner, APIs de check-in, dashboard y stats por evento.
- Admin extendido: caja, venta directa, exportaciones donde aplique ([`tickets/admin.py`](../tickets/admin.py)).
- Webhooks MercadoPago y lógica de emisión de bonos ([`tickets/processing.py`](../tickets/processing.py)).

## `events`

- Modelo `Event` (evento Fuego Austral): fechas, cupos, permisos por rol, homepage del evento.
- Términos y condiciones por evento (`EventTermsAndConditions`) y registro de aceptaciones.
- Grupos (`GrupoTipo`, `Grupo`, `GrupoMiembro`): líder, cupos de ingreso anticipado y late checkout, restricciones alimentarias.
- Auditoría: modelos registrados en `auditlog` (ver imports al final de [`events/models.py`](../events/models.py)).

## `user_profile`

- Perfiles de usuario, Mi Fuego, flujos post-login y completitud de perfil (middleware en [`tickets/middleware.py`](../tickets/middleware.py) referencia perfiles).
- **Caja legacy** por evento: listado en `/mi-fuego/cajas/` y emisión en `/mi-fuego/mis-eventos/<slug>/caja/` ([`user_profile/views.py`](../user_profile/views.py)); redirige a caja v2 si hay una sola caja activa.
- Pantalla **Mis logros** en `/mi-fuego/mis-bonos/logros/`.
- Integración con `django-allauth` para cuenta y Google.

## `caja`

- Caja v2: productos (`EventProduct`), stock unificado, múltiples puntos de venta (`EventCaja`), ventas (`CajaSale`) con efectivo, transferencia, MP QR y MP Postnet.
- Integración MercadoPago Instore (stores, POS, terminales) y reportes por evento.
- URLs bajo `/mi-fuego/` vía [`caja/urls.py`](../caja/urls.py). Detalle: [caja-v2](caja-v2.md).

## `logros`

- Definiciones de logros (`Achievement`) y desbloqueos por usuario (`UserAchievement`).
- Condiciones extensibles (hoy: `purchased_events` por IDs de evento).
- Evaluación post-pago y en Mi Fuego. Detalle: [logros](logros.md).

## `espaciozen`

- Reservas y calendario Espacio Zen: vistas y APIs REST internas bajo prefijo `/espaciozen/`.

## `utils`

- Middleware de logging, envío de email, context processors (evento actual, URL de app, Chatwoot si aplica), modelos base compartidos.

## Paquetes transversales en settings

- `bootstrap5`, `django_inlinecss`, `django_s3_storage` (medios/estáticos en entornos zappa), `auditlog`, `allauth`, `import_export`, `django_ckeditor_5`.

Para variables y despliegue, ver [configuracion](configuracion.md) y [deploy-y-ramas](deploy-y-ramas.md).
