# Logros (achievements)

Módulo `logros` para desbloquear insignias según acciones del usuario (por ejemplo, haber comprado en ciertos eventos). La UI vive en Mi Fuego; la evaluación corre al confirmar pagos y al visitar la pantalla de logros.

## Modelos

| Modelo | Rol |
|--------|-----|
| `Achievement` | Definición: `slug`, nombre, imagen estática (`img/logros/…` en `tickets/static/`, sube a S3 con `collectstatic`), descripción, `condition_type`, `condition_config` (JSON), `is_active`, `sort_order`. |
| `UserAchievement` | Logro desbloqueado por usuario; `celebration_shown` indica si ya vio el modal de celebración. |

## Condiciones (`condition_type`)

Implementadas en [`logros/conditions.py`](../logros/conditions.py):

| Tipo | Config | Regla |
|------|--------|-------|
| `purchased_events` | `{"event_ids": [9, 10, 17]}` | El usuario tiene al menos una orden **CONFIRMED** en **cada** evento listado (match por `Order.user` o `Order.email` case-insensitive). |

Para agregar condiciones nuevas: implementar checker en `CONDITION_CHECKERS` y agregar choice en `Achievement.ConditionType`.

## Cuándo se evalúan

1. **Post-pago online**: en [`payment_callback`](../tickets/views/order.py) y plantilla [`payment_callback.html`](../tickets/templates/checkout/payment_callback.html) — `check_and_unlock_for_user` + modal si hay celebraciones pendientes.
2. **Pantalla Mis logros**: `/mi-fuego/mis-bonos/logros/` — re-evalúa al cargar ([`mis_logros_view`](../user_profile/views.py)).
3. Admin Django: [`logros/admin.py`](../logros/admin.py) para ABM de definiciones.

## URLs

| Ruta | Nombre | Descripción |
|------|--------|-------------|
| `/mi-fuego/mis-bonos/logros/` | `mis_logros` | Galería de logros (desbloqueados y bloqueados) |
| `/mi-fuego/mis-bonos/logros/celebracion-vista/` | `logros_mark_celebration_shown` | `POST` JSON `{ "slugs": ["…"] }` — marca modal como visto |

## UI

- [`mis_logros.html`](../user_profile/templates/mi_fuego/my_tickets/mis_logros.html): grid de logros con estado bloqueado/desbloqueado.
- [`logro_unlocked_modal.html`](../user_profile/templates/mi_fuego/partials/logro_unlocked_modal.html): modal de celebración; también incluido en `barbu_base.html` y callback de pago.
- Enlace desde [`my_tickets/index.html`](../user_profile/templates/mi_fuego/my_tickets/index.html).

## Logro inicial (seed)

La migración [`0002_seed_tres_fiestas_oscuras`](../logros/migrations/0002_seed_tres_fiestas_oscuras.py) crea:

- **Slug**: `3-fiestas-oscuras`
- **Nombre**: “3 Fiestas oscuras”
- **Condición**: compras confirmadas en eventos `9`, `10`, `17`
- **Imagen**: `img/logros/3-oscuras.jpg` → `https://faticketera-zappa-prod.s3.amazonaws.com/img/logros/3-oscuras.jpg` tras deploy + `collectstatic`

## Servicios ([`logros/services.py`](../logros/services.py))

- `get_achievements_for_user(user)` — lista con flag `unlocked`.
- `check_and_unlock_for_user(user)` — persiste nuevos `UserAchievement`; retorna recién desbloqueados.
- `get_pending_celebrations(user)` — logros sin modal mostrado.
- `mark_celebrations_shown(user, slugs=None)` — actualiza `celebration_shown`.

Ver [casos-de-uso](casos-de-uso.md), [funcionalidades](funcionalidades.md), [glosario](glosario.md).
