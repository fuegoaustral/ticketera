# Deploy y ramas (resumen)

El detalle completo, comandos y diagramas están en el [README principal](../README.md) (sección **Deploy** y **Arquitectura**). Aquí solo reglas de equipo.

## Flujo habitual

1. Desarrollo en rama `feature/*` desde `dev`.
2. Pull request hacia `dev`: al mergear, CI/CD despliega al entorno de desarrollo (p. ej. `dev.fuegoaustral.org`).
3. Promoción a producción: PR de `dev` → `main`; al mergear, deploy automático a producción.

## Reglas que se repiten en el README

- No deploy manual con Zappa salvo emergencias documentadas.
- No push directo a `main`.
- Hotfixes desde `main` con PR y luego backport/cherry-pick a `dev`.

## Artefactos

- `collectstatic` con `deprepagos.settings_prod` en pipeline.
- Infra AWS Lambda vía Zappa (versiones en badges del README).

Para troubleshooting o Docker de emergencia, seguir el README. Este archivo evita duplicar páginas de comandos que cambian con el tiempo.
