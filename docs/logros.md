# Logros (achievements)

Módulo `logros` para desbloquear insignias según acciones del usuario (por ejemplo, haber comprado en ciertos eventos). La UI vive en Mi Fuego; la evaluación corre al confirmar pagos y al navegar la ticketera logueado.

## Modelos

| Modelo | Rol |
|--------|-----|
| `Achievement` | Definición: `slug`, nombre, imagen (upload a S3 en `logros/`), descripción, `condition_type`, `condition_config` (JSON), `is_active`, `sort_order`. |
| `UserAchievement` | Logro desbloqueado por usuario; `celebration_shown` indica si ya vio el modal de celebración. |

## Condiciones (`condition_type`)

Implementadas en [`logros/conditions.py`](../logros/conditions.py):

| Tipo | Config | Regla |
|------|--------|-------|
| `purchased_events` | `{"event_ids": [9, 10, 17]}` | El usuario tiene al menos una orden **CONFIRMED** en **cada** evento listado (match por `Order.user` o `Order.email` case-insensitive). |
| `volunteer_at_events` | `{"role": "transmutator", "must_be_used": true}` | Dueño de un bono con ese rol. Sin `event_ids`: **cualquier** evento (pasados y futuros). Con `event_ids`: al menos uno de la lista. `role`: `transmutator`, `ranger`, `caos` (`volunteer_umpalumpa`), `mad`. `must_be_used` exige que el bono se haya escaneado. |
| `attended_events` | `{"event_ids": [14, 7, 4, 1], "min_count": 2}` | Participó en **al menos** `min_count` eventos distintos: bono como **owner o holder**. `must_be_used` default `true` (escaneado). Si es `false`, también cuenta órdenes **CONFIRMED**. |

Para agregar condiciones nuevas: implementar checker en `CONDITION_CHECKERS` y agregar choice en `Achievement.ConditionType`.

## Cuándo se evalúan

1. **Navegación logueada**: el context processor [`pending_logro_celebrations`](../utils/context_processors.py) llama `evaluate_and_get_pending_payload` en GET (home, Mi Fuego, etc.) y arma el modal si hay logros sin celebrar. Así, al cargar logros nuevos en admin, el usuario los desbloquea y ve el modal al entrar a la ticketera.
2. **Post-pago online**: en [`check_order_status`](../tickets/views/order.py) y plantilla [`payment_callback.html`](../tickets/templates/checkout/payment_callback.html) — el callback de pago maneja su propia cola para no duplicar el modal.
3. **Pantalla Mis logros**: `/mi-fuego/mis-bonos/logros/` — re-evalúa al cargar ([`mis_logros_view`](../user_profile/views.py)).
4. Admin Django: [`logros/admin.py`](../logros/admin.py) para ABM de definiciones.

El modal no se dispara en admin, login, scanner, caja en vivo ni mientras hay términos pendientes.

## URLs

| Ruta | Nombre | Descripción |
|------|--------|-------------|
| `/mi-fuego/mis-bonos/logros/` | `mis_logros` | Galería de logros (desbloqueados con imagen, bloqueados sin imagen) |
| `/mi-fuego/mis-bonos/logros/celebracion-vista/` | `logros_mark_celebration_shown` | `POST` JSON `{ "slugs": ["…"] }` — marca modal como visto |

## UI

- [`mis_logros.html`](../user_profile/templates/mi_fuego/my_tickets/mis_logros.html): secciones **Desbloqueados** (imagen + descripción) y **Por desbloquear** (candado, sin imagen).
- [`logro_unlocked_modal.html`](../user_profile/templates/mi_fuego/partials/logro_unlocked_modal.html): modal de celebración incluido en [`barbu_base.html`](../tickets/templates/tickets/barbu_base.html) para cualquier página logueada.
- Enlace desde [`my_tickets/index.html`](../user_profile/templates/mi_fuego/my_tickets/index.html) y el menú de cuenta.

## Logro inicial (seed)

La migración [`0002_seed_tres_fiestas_oscuras`](../logros/migrations/0002_seed_tres_fiestas_oscuras.py) crea:

- **Slug**: `3-fiestas-oscuras`
- **Nombre**: “3 Fiestas oscuras”
- **Condición**: compras confirmadas en eventos `9`, `10`, `17`
- **Imagen**: upload en `logros/` (migración `0005` copió el estático original)

## Servicios ([`logros/services.py`](../logros/services.py))

- `get_achievements_for_user(user)` — lista con flag `unlocked` y `unlocked_at`.
- `check_and_unlock_for_user(user)` — persiste nuevos `UserAchievement`; retorna recién desbloqueados.
- `get_pending_celebrations(user)` / `pending_celebrations_payload(user)` — logros sin modal mostrado.
- `evaluate_and_get_pending_payload(user)` — desbloquea pendientes y serializa celebraciones.
- `mark_celebrations_shown(user, slugs=None)` — actualiza `celebration_shown`.

Ver [casos-de-uso](casos-de-uso.md), [funcionalidades](funcionalidades.md), [glosario](glosario.md).
