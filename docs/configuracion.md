# Configuración

Valores sensibles **no** deben commitearse: usá `.env` local basado en [`env.example`](../env.example). Esta página describe **qué** configura cada variable o bloque, no secretos.

## Base de datos

| Variable | Uso |
|----------|-----|
| `DB_HOST`, `DB_USER`, `DB_DATABASE`, `DB_PASSWORD`, `DB_PORT` | PostgreSQL (`deprepagos/settings.py`). Opcional `DB_OPTIONS` para `search_path` u otras opciones de conexión (comentado en `env.example`). |

## Entorno de la app

| Variable | Uso |
|----------|-----|
| `DEBUG` | Modo debug Django (`True`/`False`). |
| `ENV` | Etiqueta de entorno (p. ej. `local`); usada en logs y ramas de settings. |
| `APP_URL` | URL pública base (callbacks MercadoPago, enlaces en emails). |
| `SECRET_KEY` | Clave criptográfica Django (producción: valor único y secreto). |
| `ALLOWED_HOSTS` | Hosts permitidos; en código base hay defaults y `EXTRA_HOST` desde entorno. |
| `DEFAULT_FROM_EMAIL` | Remitente por defecto (definido en settings con fallback institucional). |

## Autenticación Google (OAuth)

| Variable | Uso |
|----------|-----|
| `GOOGLE_CLIENT_ID` | Client ID de la app OAuth en Google Cloud. |
| `GOOGLE_CLIENT_SECRET` | En el template `env.example`; en [`deprepagos/settings.py`](../deprepagos/settings.py) `SOCIALACCOUNT_PROVIDERS` lee el secreto como `GOOGLE_SECRET`. Alinear nombre en `.env` con lo que espera el settings activo. |

## MercadoPago

| Variable | Uso |
|----------|-----|
| `MERCADOPAGO_PUBLIC_KEY` | Front / SDK público. |
| `MERCADOPAGO_ACCESS_TOKEN` | API server-side (preferencias, consulta de pagos, Instore caja v2). |
| `MERCADOPAGO_WEBHOOK_SECRET` | Verificación HMAC de webhooks Checkout Pro (`x-signature`). |
| `MERCADOPAGO_COLLECTOR_USER_ID` | ID numérico del usuario cobrador en MercadoPago; necesario para stores/POS/QR Instore en caja v2 ([`caja/mercadopago_instore.py`](../caja/mercadopago_instore.py)). |

## Email

| Variable | Uso |
|----------|-----|
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` | SMTP (p. ej. Mailtrap en desarrollo). |

## Espacio Zen (Google Calendar API)

| Variable | Uso |
|----------|-----|
| `ESPACIO_ZEN_CLIENT_EMAIL` | Email de la cuenta de servicio. |
| `ESPACIO_ZEN_PRIVATE_KEY` | Clave privada PEM (con `\n` escapados en JSON). |

## Twilio (verificación SMS, opcional)

Definidas en settings con default vacío: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_VERIFY_SERVICE_SID`.

## Settings locales

- Copiá [`deprepagos/local_settings.py.example`](../deprepagos/local_settings.py.example) a `deprepagos/local_settings.py` para overrides y `EXTRA_INSTALLED_APPS` (ver `settings.py`).

## Producción / dev (S3)

En [`deprepagos/settings_prod.py`](../deprepagos/settings_prod.py) y [`settings_dev.py`](../deprepagos/settings_dev.py): `django_s3_storage` para `DEFAULT_FILE_STORAGE` y `STATICFILES_STORAGE`, buckets `faticketera-zappa-prod` / `faticketera-zappa-dev`. Credenciales AWS típicamente vía entorno o rol Lambda, no en este doc.
