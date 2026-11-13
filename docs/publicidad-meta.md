# Publicidad en Meta (Instagram y Facebook)

Sección **Publicidad** del panel: trae de Meta las campañas de CMR, guarda el histórico en la
base local (`data/cmr_leads.db`), avisa cuando algo anda mal y arma un reporte mensual en PDF.

Solo **lee** datos: no crea ni modifica anuncios.

## Qué hay en cada pestaña

| Pestaña | Qué muestra |
|---|---|
| Resumen y campañas | Inversión, alcance, clics, mensajes y costo por mensaje del período, gráfico por día (por mes en períodos de más de dos meses) y tabla por campaña, conjunto o anuncio. Filtro por fechas y por campaña. |
| Alertas | Sin saldo o pago pendiente, anuncio rechazado, costo por mensaje en alza, cuenta no activa y errores al actualizar. Se abren y se cierran solas. |
| Reportes | Reporte mensual con comparación contra el mes anterior, los 3 mejores y los 3 peores anuncios y recomendaciones. Se descarga en PDF. |
| Configuración | Checklist de la configuración publicitaria (estado y notas) y los pasos para conectar la cuenta. |

## Conectar la cuenta (una sola vez)

1. **App nueva.** En [developers.facebook.com/apps](https://developers.facebook.com/apps) creá una
   app de tipo **Negocio**, vinculada al Business Manager de CMR. Es aparte de la app de Instagram
   que usa CMR Leads: si Meta revisa o frena una, la otra sigue andando.
2. **Producto.** Dentro de la app agregá **API de marketing**.
3. **Usuario del sistema.** En el Business Manager: Configuración del negocio > Usuarios >
   Usuarios del sistema > Agregar (rol Administrador).
4. **Activos.** Al usuario del sistema asignale la **cuenta publicitaria** (alcanza con "ver
   rendimiento") y la **app**.
5. **Token.** Generar token > elegí la app > vencimiento **Nunca** > permisos `ads_read` y
   `business_management`.
6. **`.env`.** Pegá estos valores y reiniciá el programa (el asistente nunca toca el `.env`):

   ```env
   META_ADS_ACCESS_TOKEN=el_token_generado
   META_AD_ACCOUNT_ID=act_123456789
   ```

   El ID de la cuenta está en el Administrador de anuncios, arriba a la izquierda.

### Permisos y revisión de la app

- Se piden solo `ads_read` (leer campañas y métricas) y `business_management` (leer activos del
  Business Manager). `ads_management` hace falta recién si algún día el sistema crea o edita
  anuncios. Pedir permisos de más complica la revisión.
- Como la cuenta publicitaria es del **mismo Business Manager que la app**, con el nivel de acceso
  estándar alcanza y no hace falta mandar la app a revisión. La revisión (y el acceso avanzado)
  hace falta para manejar cuentas de **otros** negocios, por ejemplo clientes. Antes de ese paso,
  confirmá los requisitos vigentes en la documentación de Meta, porque cambian seguido.
- Si el token se filtra, revocalo desde el mismo lugar donde lo generaste y generá otro.

## Cómo se actualiza

- **Sola**: al abrir el programa y después cada `META_ADS_SYNC_HOURS` horas (6 por defecto),
  **mientras la app esté abierta**. Si la PC estuvo apagada, al abrirla completa lo que falta.
- **Con el botón** "Actualizar desde Meta".
- La primera vez trae desde el 1 de enero (para el filtro "Este año"). Después vuelve a pedir los últimos 7 días (y una
  vez por semana los últimos 28), porque Meta corrige resultados hacia atrás.
- Una sola actualización a la vez: si ya se está actualizando, el botón avisa y no hace nada.
- Tope propio de `META_ADS_MAX_REQUESTS_PER_DAY` consultas (600) y reintentos automáticos con
  espera cuando Meta pide frenar.

## Cómo leer los números

- **Mensajes**: conversaciones iniciadas desde el anuncio (WhatsApp, Messenger o Instagram). Es el
  "resultado" de las campañas de CMR.
- **Costo por mensaje** = inversión ÷ mensajes.
- **Alcance**: personas únicas. **No se suma día por día**: si la misma persona vio el anuncio el
  lunes y el martes, cuenta una vez. Por eso se guarda el alcance real de cada período (este mes,
  mes anterior, últimos 7 y 30 días y este año). Con fechas libres se pide en el momento a Meta; si no hay
  conexión aparece "s/d" (sin dato).
- Las variaciones del resumen comparan contra el período anterior de la misma duración.

## Alertas

| Alerta | Cuándo |
|---|---|
| Sin saldo | Pago pendiente o período de gracia, o al límite de gasto (o al saldo prepago) le queda menos de `META_ADS_LOW_BALANCE_ARS` ($20.000). |
| Anuncio rechazado | Meta no aprobó un anuncio o lo marcó con problemas (muestra el motivo). |
| Costo en alza | El costo por mensaje de una campaña subió más de `META_ADS_COST_ALERT_PCT` (30%) en los últimos 7 días contra los 7 anteriores, o gastó sin traer mensajes. Necesita al menos 3 mensajes en la semana anterior para comparar. |
| Cuenta no activa | Deshabilitada, en revisión o cerrada. |
| Error al actualizar | Token vencido, sin permisos, sin conexión o límite de Meta. Se cierra sola cuando vuelve a andar. |

## Reporte mensual

- Se genera **solo** a partir del día 2 de cada mes (para que Meta cierre los números del último
  día) y queda guardado. Se puede volver a generar desde la pestaña Reportes.
- Los datos del reporte quedan **congelados**: si después se re-sincroniza, el reporte no cambia
  hasta que lo regenerás.
- PDF en `data/reportes_publicidad/` (fuera de git, como el resto de `data/`).
- Recomendaciones por reglas simples: anuncios que gastaron sin traer mensajes, anuncios mucho más
  caros que el promedio, presupuesto sin gastar, frecuencia alta (más de 3), poco alcance, cambio
  del costo por mensaje contra el mes anterior y cuál conviene escalar.

## Variables de `.env`

| Variable | Por defecto | Para qué |
|---|---|---|
| `META_ADS_ACCESS_TOKEN` | (vacío) | Token del usuario del sistema |
| `META_AD_ACCOUNT_ID` | (vacío) | `act_...` o solo el número |
| `META_ADS_GRAPH_API_VERSION` | `v23.0` | Versión de la API |
| `META_ADS_SYNC_HOURS` | `6` | Cada cuántas horas se actualiza sola (0 = solo con el botón) |
| `META_ADS_MAX_REQUESTS_PER_DAY` | `600` | Tope de consultas por día |
| `META_ADS_COST_ALERT_PCT` | `30` | % de suba del costo por mensaje para alertar |
| `META_ADS_LOW_BALANCE_ARS` | `20000` | Monto mínimo antes de avisar "sin saldo" |
| `META_ADS_REPORTS_DIR` | `data/reportes_publicidad` | Dónde se guardan los PDF |

## Problemas comunes

| Mensaje | Qué hacer |
|---|---|
| "El token de Meta no es válido o venció" | Generá un token nuevo del usuario del sistema con vencimiento "Nunca". |
| "Al token le faltan permisos" | Regenerá el token marcando `ads_read` y revisá que el usuario del sistema tenga asignada la cuenta publicitaria. |
| "Meta limitó las consultas por un rato" | Esperá. Se reintenta solo en la próxima actualización. |
| Alcance "s/d" | No hay dato exacto para ese período (o no hubo conexión con fechas libres). |
