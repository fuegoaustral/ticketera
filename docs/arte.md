# Arte de Fuego Austral

El módulo reúne en una Instalación de Arte la propuesta, beca, desplegable, logística, galería y checkout del evento anual principal. Una persona puede tener varias instalaciones y sumar colaboradores por email.

## Accesos

- Participantes: `/mi-fuego/arte/`
- ESTAFA: `/mi-fuego/estafa/<evento>/`
- Configuración: Django Admin → Arte → Programas de Arte

ESTAFA es un equipo global (Django Admin → Equipos, sólo superusuarios). Cada membresía es un período con fecha de ingreso y, opcionalmente, de salida; quien sale y vuelve suma un período nuevo y el historial se conserva. Sólo las personas con una membresía activa y los superusuarios entran a ESTAFA, y únicamente sobre eventos de Fuego Austral (los que tienen voluntariado, `has_volunteers`). Ser admin de un evento no da acceso a Arte. El nexo de cada instalación se elige entre los miembros activos y ve sus instalaciones en "Asignadas a vos".

Solo el `ArtProgram` marcado como **convocatoria anual vigente** permite crear instalaciones. Los programas anteriores y sus instalaciones se conservan como histórico. El sistema impide marcar dos convocatorias como vigentes.

## Flujo

1. La persona crea la instalación o registra una instalación espontánea si cerró la inscripción ordinaria. Queda en **Inscripción pendiente**.
2. ESTAFA la aprueba (**Inscripción activa**) o la rechaza (**Rechazada**, con mensaje obligatorio) desde el listado o la revisión. El equipo recibe un email en ambos casos. Al aprobarla se crea un grupo `ARTE`; allí se administran integrantes, ingreso anticipado y late checkout con el mecanismo ya existente de grupos. Una decisión se puede volver a **Inscripción pendiente**.
3. ESTAFA asigna placement y registra el beneficio para la siguiente edición.
4. La persona solicita la beca con un presupuesto por ítems. Cada ítem es ARS o USD; los USD guardan monto, cotización ARS/USD, fecha y fuente. El valor queda congelado para mantener trazabilidad histórica.
5. Una beca aprobada se rinde con gastos por ítems, relato y al menos una foto final o de rendición. La fecha de cada gasto es la fecha real de pago.
6. El checkout se habilita en la apertura de checkout del programa o, si no tiene fecha, cuando empieza el evento: las instalaciones activas pasan a **Checkout pendiente**. El equipo sube fotos del estado final y envía el checkout (**Checkout enviado**); ESTAFA lo verifica en el predio (**Checkout verificado**).

Los comprobantes contables no se adjuntan todavía: el almacenamiento de medios actual no ofrece el aislamiento privado necesario. La galería sí pide autorización explícita de publicación en cada carga.

## Checkpoints y recordatorios

En `ArtProgram` se configuran apertura/cierre de inscripción y los cierres de propuesta, beca, desplegable, logística, checkout y rendición. Los campos vencidos quedan en lectura para participantes; ESTAFA conserva acceso administrativo.

`reminder_days` acepta una lista como `[7, 3, 1]`; una lista vacía desactiva los avisos por anticipación. Email y WhatsApp saliente se habilitan por separado. El comando diario existente es:

```bash
python manage.py send_art_reminders
```

Cada envío queda protegido por idempotencia. WhatsApp requiere las credenciales Twilio ya documentadas; no incluye un bot conversacional.

## Tipo de cambio

El MVP no consulta una fuente automáticamente porque FA debe definir qué dólar corresponde (por ejemplo BNA vendedor o MEP). La persona carga la fuente usada y ESTAFA la audita. Nunca se recalculan presupuestos o rendiciones históricos con la cotización actual.
