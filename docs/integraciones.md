# Integraciones externas

## MercadoPago

### Checkout Pro (compra online)

- SDK server-side con `MERCADOPAGO_ACCESS_TOKEN` ([`deprepagos/settings.py`](../deprepagos/settings.py) diccionario `MERCADOPAGO`).
- Preferencias de pago y consulta de pagos desde órdenes y webhooks ([`tickets/views/webhooks.py`](../tickets/views/webhooks.py)).
- `MERCADOPAGO_WEBHOOK_SECRET` para validar webhooks firmados en `POST /webhooks/mercadopago`.

### Instore (caja v2: QR y Postnet)

- API Orders/POS/Terminals en [`caja/mercadopago_instore.py`](../caja/mercadopago_instore.py).
- Alta automática de **store** por evento y **POS** por caja ([`caja/services/mercadopago_setup.py`](../caja/services/mercadopago_setup.py)).
- Ventas con `external_reference = caja-sale-{id}`; handlers en [`caja/webhook_handlers.py`](../caja/webhook_handlers.py).
- Requiere `MERCADOPAGO_COLLECTOR_USER_ID` (ID del usuario cobrador en MP). Ver [caja-v2](caja-v2.md).

## Google OAuth2 (`django-allauth`)

- Provider `google` con scope `email`.
- `GOOGLE_CLIENT_ID` y variable de secreto según settings (`GOOGLE_SECRET` en código; alinear con `.env`).

## Google Calendar (Espacio Zen)

- Service account: `ESPACIO_ZEN_CLIENT_EMAIL` y `ESPACIO_ZEN_PRIVATE_KEY` para APIs del módulo [`espaciozen`](../espaciozen/).

## Email (SMTP)

- Configuración estándar Django: `EMAIL_*` en entorno; plantillas en `TEMPLATED_EMAIL_TEMPLATE_DIR` y envío vía utilidades en `utils.email`.

## Almacenamiento S3

- En `settings_dev` / `settings_prod`: `django_s3_storage` para medios y estáticos en buckets `faticketera-zappa-dev` y `faticketera-zappa-prod`.

## Twilio

- Variables opcionales para verificación SMS (`TWILIO_*` en settings); si están vacías, esas rutas no operan hasta configurarlas.

## Chatwoot / contexto

- Context processors opcionales (`chatwoot_token`, etc.) en settings para soporte en widget; revisar [`utils/context_processors.py`](../utils/context_processors.py) si se documenta UX de soporte.

## Auditoría

- `django-auditlog` registra cambios en modelos clave (p. ej. `Event`, entidades de tickets según registro en modelos).

Para variables concretas, ver [configuracion](configuracion.md).
