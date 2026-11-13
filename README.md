# CMR Lead Finder

Herramienta de prospección y gestión de potenciales clientes locales para **CMR Software Solutions**.

Es un **mini-CRM de prospección**, no un bot de envío masivo. Encuentra negocios, los enriquece con
datos **públicos y oficiales**, los puntúa con reglas explicables y le prepara a una persona un
borrador de mensaje. **El envío siempre lo hace una persona.**

```text
Fuentes ─► Normalización ─► Dedup ─► Enriquecimiento ─► Dedup/merge ─► Exclusiones ─► Scoring
   ─► Revisión humana ─► Borrador de mensaje ─► Contacto manual (wa.me / DM) ─► Seguimiento ─► Stats
```

## Fuentes de leads

| Fuente | `source` | Cómo |
|---|---|---|
| Seguidos de CMR | `cmr_following` | Export oficial de Instagram |
| Seguidores de CMR | `cmr_followers` | Export oficial de Instagram |
| Google Maps | `google_places` | Búsqueda por rubro y zona (API oficial) |
| Carga manual | `manual` | `cmr-leads add` (con `--source-ref` = perfil que lo originó) |
| CSV | `csv_import` | `cmr-leads import csv` |
| Descubiertos desde otros perfiles | `discovered_from_*` | Reservado; ver *Limitaciones* |

**Por qué el export y no la API para seguidores/seguidos:** la API oficial de Instagram no permite
listar seguidores ni seguidos (solo da los totales). El export de *Centro de cuentas → Tu información
y permisos → Descargar tu información → Seguidores y seguidos (formato JSON)* sí los trae, es oficial
y no pone en riesgo la cuenta.

## Equipo de ventas (embudo PROSPECTAR → CONTACTAR → REUNIR → CERRAR)

Etapas: **Nuevo → Calificado → Contactado → Respondió → Reunión → Propuesta → Ganado / Perdido**
(perder siempre pide motivo: precio, timing, no le interesa, eligió a otro, sin respuesta).
El playbook con criterios, mensajes y cadencia está en [`docs/ventas-playbook.md`](docs/ventas-playbook.md).

- **Inicio → 📋 Tu día:** a quién escribirle hoy, seguimientos vencidos, negocios trabados y
  reuniones de la semana.
- **Ficha del lead → 🧭 Embudo:** etapa, próximo paso con fecha, monto (ARS) y estado de la web
  (sí / no / vieja). **🤝 Reuniones** (fecha, notas, próximos pasos) y **📄 Propuestas** armadas
  desde `config/precios.yaml`, con página imprimible → "Guardar como PDF" (sin librerías extra).
- **WhatsApp:** el botón abre `wa.me` con el mensaje precargado **y registra el contacto solo**;
  agenda seguimiento al día 3, después al 7 y sugiere cerrar al 14 (configurable). El envío
  siempre lo hacés vos.
- **📧 Emails:** cola por lotes que vos aprobás, con tope diario. Cada email se abre en tu programa
  de correo (`mailto:`); no se usa Gmail API ni se envía nada solo.
- **📈 Ventas:** KPIs por período (leads nuevos, contactados, tasa de respuesta, reuniones,
  propuestas, ganados e ingresos en ARS), embudo con conversiones, conversión por fuente y por
  rubro, y motivos de pérdida. Todo sale de la base; sin datos, se muestra vacío.

Configuración editable (reiniciá la app después de cambiarla):

| Archivo | Qué define |
|---|---|
| `config/ventas.yaml` | días de seguimiento (3 y 7), cierre (14), días máximos por etapa ("trabado"), tope de emails |
| `config/precios.yaml` | planes, variantes y precios en ARS para las propuestas |
| `config/plantillas.yaml` | mensajes por plan y rubro con `{nombre}`, `{rubro}`, `{problema_detectado}` |
| `config/catalogo.md` | lo que vendemos, para que Gemini elija la oferta y redacte |
| `config/scoring.yaml` | reglas del puntaje y del plan sugerido |

Borradores sin gastar cupo de IA: `cmr-leads export-pendientes` genera `exports/pendientes.csv`
con los calificados y su contexto; completás la columna `borrador` (por ejemplo con Claude Code) y
`cmr-leads import-borradores exports/pendientes.csv` los carga para revisar. Nada se envía.

La capa de datos de ventas está en `src/cmr_leads/sales/repository.py` (un `Protocol` +
implementación SQLAlchemy), para poder pasar a Supabase/Postgres sin reescribir las pantallas.

## OpenStreetMap: búsqueda gratis

Fuente gratis, sin key ni tarjeta (Overpass API). En **Importar → Buscar negocios** elegís rubros y
localidades y tocás **Buscar en OpenStreetMap**. En la zona tiene menos teléfonos y webs que
Google, pero no cuesta nada. Si el servidor está saturado (pasa seguido), prueba un espejo y si no,
avisa para reintentar más tarde. Los negocios que aparecen en Google y en OSM se unen solos
(mismo nombre + calle y número, o mismo nombre a menos de ~150 m).

Para cargar a mano lo que encontrás navegando Maps o Instagram: **＋ Agregar lead** o
**Importar → Planilla CSV**.

## Google Places (opcional): buscar negocios por rubro

Es la fuente principal para encontrar negocios locales. Usa la **Places API (New)** de Google, que
es oficial: buscás un rubro ("peluquería") en una zona y carga nombre, categoría, teléfono, web,
dirección, rating y link a Maps. Si el negocio puso su Instagram como sitio web, también lo toma.

1. Entrá a [console.cloud.google.com](https://console.cloud.google.com/), creá un proyecto y
   activá la facturación (Google la pide aunque no superes el cupo gratis).
2. Habilitá **Places API (New)** y creá una **API key**. Restringila a esa API.
3. Ponela en `.env` como `GOOGLE_PLACES_API_KEY` y reiniciá el programa.
4. En el panel: **Importar → Buscar negocios en Google Maps**. Por consola:
   `cmr-leads import google "peluquería" --max 60`.

Cada página de resultados (hasta 20 negocios) es una request. Google da un cupo gratis mensual
(del orden de 1.000 requests para este tipo de búsqueda; revisá la página de precios vigente).
El programa tiene un tope diario (`GOOGLE_PLACES_MAX_REQUESTS_PER_DAY`, por defecto 100) y uno
mensual (`GOOGLE_PLACES_MAX_REQUESTS_PER_MONTH`, por defecto 900, debajo de las 1.000 gratis). Para
apagar Google del todo: `GOOGLE_PLACES_ENABLED=false` (o sacá la key) y queda solo OpenStreetMap.

**Para que Google nunca cobre** (el tope de la app no frena un uso de la key desde otro lado):

1. Google Cloud Console → **APIs y servicios → Places API (New) → Cuotas y límites del sistema**.
2. Buscá la cuota de **Text Search por día** y editála a **30** (31 días × 30 = 930 < 1.000 gratis).
3. Hacé lo mismo con **Place Details por día** (la usa el mapa para ubicar locales): 300.
4. **Facturación → Presupuestos y alertas**: presupuesto de USD 1 con alerta (solo avisa, no corta).
5. Restringí la key a la Places API (New).

Los términos de Google Maps limitan qué datos se pueden guardar a largo plazo: el `place_id`
siempre se puede guardar, y el resto conviene refrescarlo periódicamente.

## Enriquecimiento

- **Instagram Business Discovery API** (oficial): bio, sitio, seguidores y publicaciones de cuentas
  profesionales a partir del username. Si la API no devuelve el perfil, es una cuenta personal o no
  existe, y el lead queda excluido. Tiene un tope diario configurable (`META_MAX_LOOKUPS_PER_DAY`).
- **Sitio web público**: una sola request a la home (respeta `robots.txt`). Detecta HTTPS, si se ve
  bien en celular, si es una tienda online y links `wa.me`, `tel:` y `mailto:`.
- **Contacto**: solo datos publicados por el negocio: links `wa.me` / `api.whatsapp.com`, teléfonos
  junto a "WhatsApp" en la bio, teléfonos y emails públicos.

### Configurar la API de Instagram

1. La cuenta de Instagram de CMR tiene que ser **profesional** (Business o Creator) y estar
   **vinculada a una página de Facebook**.
2. Crear una app en [developers.facebook.com](https://developers.facebook.com/) con el producto
   *Instagram* (login con Facebook) y permisos `instagram_basic` y `pages_show_list` /
   `pages_read_engagement`.
3. Generar un token (idealmente de **System User** en Business Manager, porque los tokens de usuario
   largos vencen a los 60 días) y obtener el ID de la cuenta de Instagram profesional.
4. Completar `META_ACCESS_TOKEN` e `INSTAGRAM_BUSINESS_ACCOUNT_ID` en `.env`.

Revisá la documentación vigente de Meta: los permisos y las versiones de la API cambian seguido.

## Scoring

Configurable en [`config/scoring.yaml`](config/scoring.yaml):

1. **Exclusiones duras**: cuenta propia o clientes (`usernames`), competidores, instituciones,
   creadores de contenido y cuentas no comerciales.
2. **FIT (0-40)**: ¿es un negocio que CMR atiende? (cuenta profesional, categoría, señales
   comerciales, rubro, si es local).
3. **NEED (0-40)**: ¿tiene una necesidad clara? Sin web, solo link en la bio, web lenta o vieja,
   pedidos por mensaje, turnos.
4. **REACH (0-20)**: ¿se lo puede contactar? WhatsApp publicado > teléfono > email > solo DM.

Total 0-100 → `baja` (<40) · `posible` (40-69) · `interesante` (≥70). Cada lead guarda el desglose
punto por punto (`cmr-leads leads show <id>`) y además se le sugiere un servicio: `pagina_web`,
`tienda_online`, `sistema_turnos`, `mejora_web` o `sistema_a_medida`.

## Deduplicación

Cada lead registra identificadores normalizados únicos por workspace:

- ID de Instagram y username
- teléfono / WhatsApp, con la misma clave con o sin el 9 de celular
- dominio del sitio (sin contar Linktree, wa.me ni redes sociales)
- email

Si un dato nuevo coincide con otro lead, se **fusionan** conservando todas las fuentes,
interacciones e historial. Los nombres parecidos se listan con `leads duplicates` para fusionarlos
a mano (`leads merge`).

## IA con Gemini (opcional, cuota gratis)

Con `GEMINI_API_KEY` en `.env` (sacala en [aistudio.google.com/apikey](https://aistudio.google.com/apikey))
Gemini redacta el primer mensaje, elige la oferta con más chances por lead y clasifica las
respuestas. **Usá solo la cuota gratuita de Google AI Studio:** no actives facturación en ese
proyecto; así, si se agota la cuota, Gemini devuelve error y no cobra nada.

Siempre hay respaldo: si Gemini falla o no hay key, el mensaje sale de `config/plantillas.yaml`,
el plan del scoring por reglas y la clasificación de respuestas por palabras clave. A la IA no se le
mandan teléfonos, emails ni documentos (se recortan antes) y nunca envía nada sola.
`ANTHROPIC_API_KEY` queda como alternativa para clasificar respuestas.

## Publicidad en Meta (opcional)

Sección **Publicidad** del panel (`/publicidad`): lee las campañas de CMR en Instagram y Facebook
con la API de marketing de Meta (solo lectura) y guarda el histórico en la base local.

- **Resumen y campañas**: inversión, alcance, clics, mensajes y costo por mensaje, con filtro de
  fechas y detalle por campaña, conjunto o anuncio.
- **Alertas**: sin saldo, anuncio rechazado, costo por mensaje en alza y errores al actualizar.
- **Reporte mensual** con comparación, mejores y peores anuncios, recomendaciones y PDF con la marca
  de CMR (se genera solo desde el día 2).
- **Checklist** de configuración publicitaria con estado y notas.

Se activa con `META_ADS_ACCESS_TOKEN` y `META_AD_ACCOUNT_ID` en `.env` (token de un usuario del
sistema de una app de Meta aparte, con `ads_read` y `business_management`). Se actualiza sola cada
6 horas con la app abierta. Guía completa: [`docs/publicidad-meta.md`](docs/publicidad-meta.md).

## Contacto: WhatsApp

`cmr-leads message <id>` genera un **borrador** según el servicio sugerido y un **link click-to-chat**
(`https://wa.me/<numero>?text=...`). Al abrirlo, WhatsApp muestra el mensaje precargado y una persona
lo revisa y lo envía. Si el lead no tiene WhatsApp, se sugiere un DM de Instagram (`ig.me/m/<usuario>`).

**Por qué no WhatsApp Cloud API para el primer contacto:** los mensajes que inicia la empresa
requieren plantillas aprobadas y **consentimiento previo** del destinatario. Usarla en frío viola la
política de WhatsApp y puede bloquear el número. Queda para la fase 4, solo con quien haya respondido.

Buenas prácticas: pocos mensajes por día, siempre personalizados, respetar el "no me escribas"
(`cmr-leads dnc <id>`) y consultar el Registro Nacional No Llame (Ley 26.951).

## Instalación

```bash
uv venv && source .venv/bin/activate      # o: python -m venv .venv
uv pip install -e ".[dev]"                # o: pip install -e ".[dev]"
cp .env.example .env                      # completar valores
cmr-leads init
```

## Arranque rápido (doble clic)

Necesitás tener [Python 3.11+](https://www.python.org/downloads/) instalado (en Windows, marcá "Add Python to PATH").

- **Windows:** doble clic en `iniciar.bat`.
- **Mac / Linux:** `./iniciar.sh`.

La primera vez instala todo (un par de minutos). Después abre el panel en el navegador.

## Panel web

```bash
cmr-leads web            # abre http://127.0.0.1:8000
```

- **Inicio**: resumen, mejores leads y seguimientos pendientes.
- **Leads**: buscador, filtros y links directos al Instagram, WhatsApp, web, Google y Maps de cada negocio.
- **Revisar**: modo rápido, un lead por vez (atajos `A` aprobar, `D` descartar con motivo, `S` saltar).
- **Detalle**: por qué tiene ese puntaje, mensaje editable, botón "Abrir en WhatsApp" con el texto cargado y "Marcar como contactado".
- **Algoritmo**: aprende de lo que aprobás y descartás. Muestra qué reglas aciertan, sugiere subir o bajar pesos (las aplicás vos) y deja editarlos a mano. Los ajustes se guardan en `config/scoring.local.yaml`, que no se sube a git. Si descartás un lead como "Ya es cliente" o "Competidor", esa cuenta queda excluida.
- **Respuestas**: muestra quién contestó, qué dijo (interesado, pide info, ahora no, no le interesa, no volver a escribir o respuesta automática), las objeciones más comunes y qué mensaje, rubro, servicio, fuente y canal consiguen más respuestas. Las respuestas se cargan en cada lead pegando el texto o subiendo el chat exportado de WhatsApp (en el celu: chat → ⋮ → Más → Exportar chat → Sin archivos). Si pide no recibir más mensajes, el lead queda en "no contactar". Lo que responden también alimenta el algoritmo.
- **Instagram**: con la API configurada, el botón "Actualizar desde Instagram" trae seguidores, alcance, visitas al perfil, clics al link y las últimas publicaciones con su alcance, interacciones y guardados. Muestra qué formato, día y horario funcionan mejor, con recomendaciones. Si cargás `FACEBOOK_PAGE_ID` y el permiso de mensajes, también lee los DM: registra y clasifica las respuestas de tus leads, y crea un lead nuevo con cada negocio que te escribió primero. Por consola: `cmr-leads instagram-sync`.
- **Importar**: subir el export de Instagram o un CSV, completar datos y exportar a Excel.

El panel corre solo en tu compu (`127.0.0.1`) y todavía no tiene login (llega en la fase 2).

## Uso por consola

```bash
cmr-leads import instagram-export ~/Descargas/instagram-cmr.zip --account cmr.software
cmr-leads import csv relevamiento.csv
cmr-leads add estetica.sofi --city "San Nicolás" --source-ref cmr.software

cmr-leads enrich --limit 20              # Business Discovery + sitio web, re-puntúa
cmr-leads score --all                    # re-puntuar tras cambiar config/scoring.yaml

cmr-leads leads list --min-score 70
cmr-leads leads show 12
cmr-leads review                          # aprobar / rechazar / no contactar

cmr-leads message 12                      # borrador + link wa.me
cmr-leads contacted 12 --channel whatsapp
cmr-leads reply 12 "Hola! cuánto sale?"     # registra y clasifica la respuesta
cmr-leads import whatsapp 12 "Chat de WhatsApp con Pelu.txt"

cmr-leads export --format xlsx
cmr-leads stats                           # qué fuentes generan mejores leads
```

`python main.py <comando>` también funciona sin instalar el paquete.

### CSV

Columnas reconocidas (separador `,` o `;`): `instagram_username`, `display_name`, `bio`, `category`,
`phone`, `whatsapp`, `email`, `website`, `city`, `province`, `country`, `address`, `notes`.

## Estructura

```text
config/scoring.yaml            reglas de scoring
src/cmr_leads/
  cli.py                       comandos (Typer)
  settings.py                  configuración (.env)
  services.py                  casos de uso: estados, contacto, stats
  db/                          modelos SQLAlchemy, sesión, migraciones Alembic
  domain/                      enums y schemas Pydantic
  sources/                     fuentes intercambiables (export IG, CSV)
  enrichment/                  Business Discovery, sitio web, extracción de contactos
  pipeline/                    normalización, dedup, ingesta, enriquecimiento, scoring, topes
  messaging/                   generador de borradores + plantillas Jinja2
  exports/                     CSV / Excel
tests/
```

Agregar una fuente nueva (por ejemplo Google Places) = una clase en `sources/` con `discover()`
que devuelva `SourceRecord`. El resto del pipeline no cambia.

## Backup y restauración

Al abrir la app se hace **un backup por día** de la base en `BACKUP_DIR` (por defecto
`data/backups`, conserva 30 días con `BACKUP_KEEP_DAYS`). Podés apuntarlo a una carpeta de
Google Drive u OneDrive para tener copia fuera de la PC. Usa la API de backup de SQLite, así que
la copia es consistente aunque la app esté abierta.

Para restaurar: cerrá la app, copiá el backup elegido (`cmr_leads_AAAA-MM-DD.db`) sobre
`data/cmr_leads.db` y volvé a abrirla. Las migraciones solo agregan columnas y tablas y son
reversibles (`alembic downgrade -1`).

## Base de datos

SQLite por defecto. Para pasar a PostgreSQL, alcanza con cambiar `DATABASE_URL` y correr
`cmr-leads init`. Si cambiás los modelos, generá una migración nueva:

```bash
alembic revision --autogenerate -m "descripcion"
```

## Desarrollo

```bash
pytest
ruff check . && ruff format --check .
```

## Limitaciones conocidas

- Listar seguidores o seguidos de **otros** perfiles no es posible con APIs oficiales, así que no
  está implementado. Los `source` `discovered_from_*` quedan reservados para cargas manuales
  (`add --source-ref`) o futuras fuentes oficiales.
- Business Discovery no devuelve categoría, teléfono ni dirección. Esos datos salen de la bio, del
  sitio o de la carga manual (en la fase 2, de Google Places).

## Pruebas manuales y mejoras a futuro

- Checklist por etapa: [`docs/checklist-pruebas.md`](docs/checklist-pruebas.md).
- Mejoras pagas posibles (no implementadas): [`docs/mejoras-pagas.md`](docs/mejoras-pagas.md).

## Roadmap

1. **MVP CLI** (este repo): export, CSV, carga manual, Business Discovery, sitio web, scoring,
   revisión, borradores, contacto manual, export y stats.
2. **Descubrimiento local + panel**: fuente Google Places, panel FastAPI + HTMX, login y
   recordatorios de seguimiento.
3. **IA híbrida**: clasificación de casos dudosos y borradores con IA, recalibración del scoring
   con resultados reales.
4. **Escala**: PostgreSQL, múltiples workspaces y roles, WhatsApp Cloud API solo con consentimiento,
   integraciones.
