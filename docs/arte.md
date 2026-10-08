# Arte de Fuego Austral

El módulo reúne en una Instalación de Arte la propuesta, beca, desplegable, logística, galería y checkout del evento anual principal. Una persona puede tener varias instalaciones y sumar a su equipo a otras personas con cuenta.

## Accesos

- Participantes: `/mi-fuego/arte/`
- ESTAFA: `/mi-fuego/estafa/<evento>/`
- Configuración: Django Admin → Arte → Programas de Arte

ESTAFA es un equipo global (Django Admin → Equipos, sólo superusuarios). Cada membresía es un período con fecha de ingreso y, opcionalmente, de salida; quien sale y vuelve suma un período nuevo y el historial se conserva. Sólo las personas con una membresía activa y los superusuarios entran a ESTAFA, y únicamente sobre eventos de Fuego Austral (los que tienen voluntariado, `has_volunteers`). Ser admin de un evento no da acceso a Arte. Cada instalación puede tener un **contacto de ESTAFA**, elegido entre los miembros activos desde la revisión. Acompaña al equipo durante todo el proceso: el equipo ve su nombre y email en la instalación y recibe un email al asignarlo; el contacto recibe un email cuando el equipo envía el checkout. El contacto ve sus instalaciones en "Asignadas a vos". Cualquier miembro de ESTAFA puede verificar el checkout.

## Equipo de la instalación

Cada instalación tiene su equipo: un `Grupo` de tipo `ARTE` (en Django Admin, Events → Equipos) que se crea al inscribirla, con la persona responsable como líder. Es el mismo mecanismo que usan CAOS y los camps para el ingreso anticipado y el late checkout.

- Se suma gente por email o DNI de una cuenta existente, sin invitaciones; la persona recibe un email.
- Sumarse no requiere bono: la instalación se arma antes de la venta. Sí lo requieren el ingreso anticipado y el late checkout. Desde que empieza la venta, la lista marca a quienes todavía no tienen un bono a su nombre.
- Por defecto las personas sumadas sólo ven la instalación. La persona responsable (o ESTAFA) les da permiso de edición (`Artwork.collaborators`). Quienes editan pueden sumar y quitar personas, pero no a quienes editan.
- El ingreso anticipado y el late checkout se siguen cargando desde Mis bonos → Mis Equipos.

Solo el `ArtProgram` marcado como **convocatoria anual vigente** permite crear instalaciones. Los programas anteriores y sus instalaciones se conservan como histórico. El sistema impide marcar dos convocatorias como vigentes.

## Flujo

1. La persona crea la instalación o registra una instalación espontánea si cerró la inscripción ordinaria. Queda en **Inscripción pendiente**.
2. ESTAFA la aprueba (**Inscripción aprobada**) o la rechaza (**Rechazada**, con mensaje obligatorio) desde el listado o la revisión. El equipo recibe un email en ambos casos. Una decisión se puede volver a **Inscripción pendiente**. El contacto de ESTAFA se asigna en cualquier estado y se conserva aunque cambie el estado.
3. ESTAFA asigna placement y registra el beneficio para la siguiente edición.
4. La persona solicita la beca con un presupuesto por ítems. Cada ítem es ARS o USD; los USD guardan monto, cotización ARS/USD, fecha y fuente. El valor queda congelado para mantener trazabilidad histórica.
5. Una beca aprobada se rinde con gastos por ítems, relato y al menos una foto final o de rendición. La fecha de cada gasto es la fecha real de pago.
6. El checkout se habilita en la apertura de checkout del programa: las instalaciones aprobadas pasan a **Checkout pendiente**. El equipo sube fotos del estado final y envía el checkout (**Checkout enviado**); ESTAFA lo verifica en el predio (**Checkout verificado**).

Los comprobantes contables no se adjuntan todavía: el almacenamiento de medios actual no ofrece el aislamiento privado necesario. La galería sí pide autorización explícita de publicación en cada carga.

## Checkpoints y recordatorios

En `ArtProgram` se configuran la apertura y el cierre de inscripción, beca, logística, galería y checkout, y los cierres de propuesta, desplegable, declaración y rendición. Los campos vencidos quedan en lectura para participantes; ESTAFA conserva acceso administrativo.

Los pasos sólo usan fechas del programa, con una regla única: una apertura vacía deja el paso sin habilitar ("Te avisamos cuando se habilite") y un cierre vacío, sin cierre. Los pasos sin apertura (detalles, equipo, desplegable, declaración) se abren al guardar la instalación. Las fechas del evento quedan para los bonos: si empezó la venta, si alguien tiene bono, la fecha máxima de ingreso anticipado y el límite de carga de ingresos anticipados del evento.

`reminder_days` acepta una lista como `[7, 3, 1]`; una lista vacía desactiva los avisos por anticipación. Email y WhatsApp saliente se habilitan por separado. El comando diario existente es:

```bash
python manage.py send_art_reminders
```

Cada envío queda protegido por idempotencia. WhatsApp requiere las credenciales Twilio ya documentadas; no incluye un bot conversacional.

## Tipo de cambio

El MVP no consulta una fuente automáticamente porque FA debe definir qué dólar corresponde (por ejemplo BNA vendedor o MEP). La persona carga la fuente usada y ESTAFA la audita. Nunca se recalculan presupuestos o rendiciones históricos con la cotización actual.
