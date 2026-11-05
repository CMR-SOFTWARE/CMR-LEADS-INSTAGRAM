# Playbook de ventas — CMR Software Solutions

Cómo trabajamos los leads en CMR Lead Finder: a quién escribirle primero, qué decirle, cuándo
insistir y cuándo soltar. Pensado para pymes de San Nicolás, Ramallo y Villa Constitución.

> Basado en buenas prácticas de los skills de ventas oficiales de Anthropic
> (`anthropics/knowledge-work-plugins`, plugin `sales`: lead-triage, draft-outreach,
> daily-briefing, call-prep, call-summary, pipeline-review, forecast, handle-objection),
> adaptadas a venta local por WhatsApp. No se instaló ni ejecutó nada de esos repos.

---

## 1. El embudo

| Etapa | Qué significa | Sale de la etapa cuando… |
|---|---|---|
| **Nuevo** | Lo encontró el sistema (Google, OSM, CSV, carga manual) | Lo revisamos |
| **Calificado** | Encaja con lo que vendemos y tiene forma de contacto | Le mandamos el primer mensaje |
| **Contactado** | Ya le escribimos | Responde, o lo damos por perdido |
| **Respondió** | Contestó algo (aunque sea "pasame precio") | Agendamos reunión o se cae |
| **Reunión** | Tenemos (o tuvimos) una charla / visita | Le mandamos propuesta |
| **Propuesta** | Tiene un presupuesto en ARS | Acepta o rechaza |
| **Ganado / Perdido** | Cerrado. **Perdido siempre con motivo** | — |

Regla de oro: **todo lead activo tiene un próximo paso con fecha.** Si no lo tiene, está trabado.

## 2. Calificar: a quién escribirle primero

Se miran dos cosas, igual que en `lead-triage`: **encaje** (¿le sirve lo que vendemos?) e
**intención** (¿hay señales de que lo necesita ahora?).

**Encaje (lo calcula el scoring, `config/scoring.yaml`):**
- Rubro donde tenemos producto armado: estética/peluquería/consultorio → **CMR Nexo**;
  canchas/clubes → **CMR Canchas**; comercio con stock o que vende por mensaje → **Plan Gestión**;
  solo Instagram o sin web → **Plan Web**.
- Es de la zona (San Nicolás, Ramallo, Villa Constitución).
- Tiene forma de contacto (WhatsApp ideal; Instagram o email sirven).

**Intención (señales):**
- No tiene web, o la web no anda / no se adapta al celular.
- Toma turnos o pedidos por WhatsApp/Instagram ("pedí tu turno por DM", "hacé tu pedido").
- Negocio activo (publica seguido, tiene reseñas recientes).

**Prioridad:**
- **A (escribir hoy):** encaje alto + alguna señal clara (ej.: peluquería que da turnos por DM).
- **B (esta semana):** encaje alto sin señal, o encaje medio con señal.
- **C (cuando haya tiempo):** encaje dudoso o sin contacto directo.
- **Descartar:** competencia (agencias, programadores), instituciones, cerrado, pidió no contacto.

El score y el plan sugerido **siempre llevan el motivo escrito**: si no podés explicar por qué,
no es una buena prioridad.

## 3. Primer mensaje (WhatsApp)

Estructura de `draft-outreach`, achicada para WhatsApp:

1. **Línea de relevancia:** algo concreto de *su* negocio que demuestre que lo miramos
   ("vi que los turnos los toman por Instagram"). Nunca "encontré tu negocio".
2. **Puente de valor:** una oración con lo que ganarían ellos en el día a día, no lo que hacemos
   nosotros. Si hay un cliente parecido (Canito Skin, Planeta Surf), se puede nombrar.
3. **Pedido chico:** una pregunta fácil de contestar ("¿les interesa que les muestre cómo
   quedaría?"). Nada de "agendemos 30 minutos".

Reglas:
- **Máximo 50–60 palabras.** Tratar de "ustedes". Sin emojis, sin precios, sin links.
- Prohibido: "espero que estés bien", "quería contactarte", "solución integral",
  "transformación digital", "potenciar".
- No inventar nada que no esté en la ficha. No mencionar estrellas ni reseñas de Google.
- **Siempre lo manda una persona.** El sistema arma el borrador y abre wa.me; vos tocás enviar.

## 4. Cadencia de seguimiento

| Día | Acción | Tono |
|---|---|---|
| 0 | Primer mensaje | Relevancia + valor + pregunta |
| 3 | Seguimiento 1 | Corto, sumá un dato nuevo ("les dejo un ejemplo de cómo quedó Canito Skin") |
| 7 | Seguimiento 2 | Más directo: "¿lo vemos más adelante o no les interesa por ahora?" |
| 14 | Cierre | Si no respondió: **Perdido — sin respuesta**. Se puede reintentar en 3–4 meses |

- Si responde "no": se agradece, se marca **Perdido** con motivo y **no se insiste**.
- Si pide no recibir mensajes: **No contactar**, para siempre.
- Tiempo de respuesta cuando el lead contesta: **el mismo día** (como el P0 de `lead-triage`).

## 5. Objeciones frecuentes

Primero entender qué tipo es (`handle-objection`): no es lo mismo una objeción que una
negociación.

| Lo que dicen | Qué hay detrás | Cómo responder |
|---|---|---|
| "Está caro" | Precio/valor | Volver a *su* problema y su costo (turnos perdidos, horas contestando). Ofrecer lo más chico (Plan Web o Nexo Inicial) y crecer después |
| "Ahora no" | Timing | Preguntar cuándo, agendar fecha de seguimiento concreta |
| "Ya tengo página" | Status quo | Mirar la web: si no anda en celular o no toma turnos, mostrarlo con un ejemplo |
| "Lo tengo que consultar" | Autoridad | Ofrecer pasar la propuesta por escrito para que la vea la otra persona |
| "No sé si me sirve" | Riesgo/confianza | Ejemplo real de un negocio parecido de la zona; demo corta |
| "Me lo hace un conocido" | Competencia | No pelear: dejar la puerta abierta y ofrecer soporte si algo falla |

## 6. Antes de una reunión (`call-prep`)

Revisar en la ficha del lead: qué ofrecerle y por qué, su web/Instagram, qué contestó, objeciones
que ya aparecieron. Llevar **2–3 preguntas** para entender cómo trabajan hoy (cómo toman turnos,
cómo venden, qué les quita tiempo) y **un ejemplo** de un cliente parecido.

## 7. Después de la reunión (`call-summary`)

Registrar en el sistema el mismo día: **qué se decidió, qué se comprometió cada uno, preguntas
abiertas, objeciones y el próximo paso con fecha.** Si quedó en presupuesto: armar la
propuesta en ARS en menos de 48 h.

## 8. Revisión semanal del embudo (`pipeline-review` / `forecast`)

Cada semana mirar:
- **Trabados:** leads que superan los días máximos de su etapa o sin próximo paso.
- **Sin actividad 14+ días** en etapas activas.
- **Propuestas abiertas:** cuáles están cerca de cerrarse y qué falta.
- **Motivos de pérdida** más frecuentes: si se repite "precio", revisar la oferta; si se repite
  "sin respuesta", revisar los mensajes.
- **Conversión por rubro y por fuente:** dónde conviene buscar más.

## 9. Briefing diario (`daily-briefing`)

Lo primero del día, en el Inicio del sistema:
1. **A quién escribirle hoy** (los mejores leads calificados sin contactar).
2. **Seguimientos vencidos** (día 3 / día 7).
3. **Negocios trabados.**
4. Reuniones del día y propuestas por vencer.

Todos los números del panel salen de la base real. Si no hay datos, se muestra vacío: nunca
números de ejemplo.
