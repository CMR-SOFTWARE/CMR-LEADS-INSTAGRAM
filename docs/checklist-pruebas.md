# Checklist de pruebas manuales — Equipo de ventas

Antes de probar: abrí la app con `iniciar.bat` y confirmá que en `data/backups` apareció el backup
de hoy (`cmr_leads_AAAA-MM-DD.db`).

## Prospectar
- [ ] **Importar → Buscar negocios → OpenStreetMap** con "peluquería" en San Nicolás: carga leads
      o muestra "OpenStreetMap está saturado…" (reintentar más tarde).
- [ ] La misma búsqueda en Google (si está activo): los negocios repetidos **no se duplican**.
- [ ] El contador "Consultas a Google" sube y respeta los topes (día / mes).
- [ ] **＋ Agregar lead** a mano y **Planilla CSV**: aparecen en Leads con puntaje y plan sugerido.
- [ ] En la ficha: el puntaje tiene el motivo de cada punto y "Web: Sí / No / Vieja".
- [ ] **🎯 Analizar con IA** muestra oferta, % y motivo. Sin key de Gemini, el plan por reglas sigue.

## Contactar
- [ ] Calificar un lead (✓) → aparece en **Inicio → Tu día → A quién escribirle hoy**.
- [ ] El borrador usa la plantilla del plan/rubro y menciona el problema detectado, sin `{…}`.
- [ ] **Abrir en WhatsApp**: abre wa.me con el mensaje y el lead pasa a **Contactado** con
      próximo paso "Seguimiento 1 (día 3)" — sin tocar otro botón.
- [ ] Registrar otro contacto: el próximo paso pasa a día 7, y después a "Cerrar si no responde".
- [ ] **📧 Emails**: aprobar un lote respeta el tope diario; "Abrir en mi correo" abre el mail
      armado; "Ya lo mandé" lo pasa a Contactado.
- [ ] `cmr-leads export-pendientes` → completar `borrador` → `cmr-leads import-borradores …`:
      el borrador aparece en la ficha.

## Seguimiento
- [ ] Cambiar la fecha del próximo paso a ayer: aparece en **Seguimientos vencidos**.
- [ ] Un lead que supera los días de su etapa (`config/ventas.yaml`) aparece en **Negocios trabados**.

## Reunir
- [ ] **🤝 Registrar reunión** con notas y próximos pasos: el lead pasa a **Reunión** y la
      reunión aparece en el historial y en "Tu día" si es en los próximos 7 días.

## Cerrar
- [ ] **📄 Armar propuesta** (ej.: CMR Nexo Profesional): el precio sale de `config/precios.yaml`,
      el lead pasa a **Propuesta** y el monto se completa.
- [ ] En la propuesta: **Guardar como PDF** genera el PDF prolijo (sin menú lateral).
- [ ] Marcar la propuesta como **aceptada** → el lead pasa a **Ganado**.
- [ ] Elegir **Perdido** sin motivo: no deja guardar. Con motivo: guarda y aparece en
      **Ventas → Motivos de pérdida**.

## Dashboard
- [ ] **📈 Ventas**: los números coinciden con lo que hiciste; el embudo siempre baja etapa a
      etapa y los % son la conversión desde la etapa anterior.
- [ ] Período sin actividad: se ve "Todavía no hay actividad…" (nunca números de ejemplo).
