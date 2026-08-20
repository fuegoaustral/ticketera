# Arte de Fuego Austral

El módulo reúne en un expediente por obra la propuesta, beca, desplegable, logística, galería y checkout del evento anual principal. Una persona puede tener varias obras y sumar colaboradores por email.

## Accesos

- Participantes: `/mi-fuego/arte/`
- Coordinación: `/mi-fuego/mis-eventos/<evento>/arte/`
- Configuración: Django Admin → Eventos → Programas de Arte

Solo el `ArtProgram` marcado como **convocatoria anual vigente** permite crear obras. Los programas anteriores y sus obras se conservan como histórico. El sistema impide marcar dos convocatorias como vigentes.

## Flujo

1. La persona crea un borrador o registra una obra espontánea si cerró la inscripción ordinaria.
2. Al enviar la propuesta se crea un grupo `ARTE`; allí se administran integrantes, ingreso anticipado y late checkout con el mecanismo ya existente de grupos.
3. La coordinación revisa la obra, pide cambios, acepta o rechaza, asigna placement y registra el beneficio para la siguiente edición.
4. La persona solicita la beca con un presupuesto por ítems. Cada ítem es ARS o USD; los USD guardan monto, cotización ARS/USD, fecha y fuente. El valor queda congelado para mantener trazabilidad histórica.
5. Una beca aprobada se rinde con gastos por ítems, relato y al menos una foto final o de rendición. La fecha de cada gasto es la fecha real de pago.
6. Al terminar, la persona solicita checkout y la coordinación lo verifica.

Los comprobantes contables no se adjuntan todavía: el almacenamiento de medios actual no ofrece el aislamiento privado necesario. La galería sí pide autorización explícita de publicación en cada carga.

## Checkpoints y recordatorios

En `ArtProgram` se configuran apertura/cierre de inscripción y los cierres de propuesta, beca, desplegable, logística, checkout y rendición. Los campos vencidos quedan en lectura para participantes; la coordinación conserva acceso administrativo.

`reminder_days` acepta una lista como `[7, 3, 1]`; una lista vacía desactiva los avisos por anticipación. Email y WhatsApp saliente se habilitan por separado. El comando diario existente es:

```bash
python manage.py send_art_reminders
```

Cada envío queda protegido por idempotencia. WhatsApp requiere las credenciales Twilio ya documentadas; no incluye un bot conversacional.

## Tipo de cambio

El MVP no consulta una fuente automáticamente porque FA debe definir qué dólar corresponde (por ejemplo BNA vendedor o MEP). La persona carga la fuente usada y la coordinación la audita. Nunca se recalculan presupuestos o rendiciones históricos con la cotización actual.
