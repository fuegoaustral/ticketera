# Product

## Register

product

## Users

Artistas y equipos de obra completan inscripciones, becas, logística, check-in y checkout principalmente desde el teléfono. Coordinación de Arte y responsables de becas revisan expedientes, montos, evidencias y estados principalmente desde computadora, con apoyo móvil durante el evento.

## Product Purpose

Ticketera organiza el expediente completo de cada obra de Fuego Austral: propuesta, seguridad, carta de entendimiento, beca, rendición, logística, placement y checkout. La interfaz debe hacer evidente qué falta, quién puede actuar y qué dato queda registrado.

## Brand Personality

Clara, comunitaria y confiable. La interfaz acompaña sin burocracia y transmite trazabilidad cuando hay dinero, permisos o evidencia.

## Anti-references

Evitar la apariencia de planilla administrativa sin jerarquía, formularios interminables sin progreso, tarjetas anidadas, etiquetas ambiguas y controles que dependan sólo del color. No convertir el backoffice en una copia genérica de Django Admin.

## Design Principles

- Una pantalla, una tarea principal y un siguiente paso visible.
- Mostrar estado, responsable y fecha junto al dato que se revisa.
- Los montos siempre muestran moneda, separadores locales y total convertido antes de guardar.
- Progresivo: primero el resumen, luego el detalle bajo demanda.
- La misma acción debe verse y funcionar igual en móvil y escritorio.

## Accessibility & Inclusion

Apuntar a WCAG 2.2 AA: contraste suficiente, foco visible, navegación por teclado, etiquetas asociadas, mensajes de error junto al campo y controles táctiles de al menos 44 px. No depender sólo del color para comunicar estados y respetar `prefers-reduced-motion`.
