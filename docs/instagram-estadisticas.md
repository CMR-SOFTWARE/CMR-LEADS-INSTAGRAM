# Estadísticas de Instagram

Pestaña **Instagram** del panel: cómo le va a la cuenta de CMR, qué formato funciona y cuándo
conviene publicar. Usa la misma conexión que ya tenía CMR Leads (`META_ACCESS_TOKEN` e
`INSTAGRAM_BUSINESS_ACCOUNT_ID`).

## Filtros

- **Período**: este mes, mes anterior, últimos 7 y 30 días, este año o fechas libres.
- **Formato**: reel, imagen, carrusel o historia. Todo lo de la pantalla y el análisis sigue los
  filtros.

## Qué muestra

| Bloque | Qué es |
|---|---|
| Números de arriba | Visualizaciones, alcance por día (promedio), visitas al perfil, clics al link de la bio, toques a botones e interacciones, contra el período anterior. |
| Visualizaciones por día | Vistas, visitas al perfil y clics (por mes en períodos largos). |
| A dónde entran | Clics al link de la bio y toques a cada botón del perfil (llamar, mail, cómo llegar, etc.). Meta da cantidades, **no quién** tocó. |
| Qué formato funciona más | Vistas, alcance e interacciones promedio por formato, con su mejor día y horario. |
| Día y horario por formato | Vistas promedio según el día y la franja en que se subió, y el cruce día por horario. Con 1 o 2 publicaciones por casillero el dato es orientativo. |
| Historias | Cuántas se subieron por día, vistas por historia, por día de la semana y mejor horario. |
| Qué conviene hacer | Análisis con Gemini de los números filtrados: qué publicar, cuándo y con qué frecuencia. |

## Cómo se actualiza

- **Botón "Actualizar desde Instagram"**: publicaciones del año, historias activas y métricas día
  por día. La primera vez completa el año de a tandas (60 días por vez) para no pasar el tope
  diario de consultas (`META_MAX_STATS_REQUESTS_PER_DAY`).
- **Historias**: Meta solo deja leer cada historia mientras está activa (24 h). Con la app abierta
  se leen cada `INSTAGRAM_STORIES_MINUTES` minutos (60 por defecto). Las historias de días con la
  compu apagada pueden faltar, y no se pueden recuperar historias anteriores a la primera lectura:
  los promedios de historias cuentan desde ese día.
- Las horas se muestran en horario de Argentina.

## Lo que no se puede (límites de Meta)

- Saber **quién** tocó un link: solo hay cantidades.
- Historias viejas: solo las activas en el momento de leer.

## Variables de `.env`

| Variable | Por defecto | Para qué |
|---|---|---|
| `META_GRAPH_API_VERSION` | `v23.0` | Versión de la API. Si queda mal escrita (`v26.` o `26`) el programa la corrige a `v26.0`. |
| `INSTAGRAM_STORIES_MINUTES` | `60` | Cada cuántos minutos se leen las historias activas (0 = nunca). |
| `META_MAX_STATS_REQUESTS_PER_DAY` | `300` | Tope de consultas por día para estadísticas. |
