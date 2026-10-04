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
| Carga manual | `manual` | `cmr-leads add` (con `--source-ref` = perfil que lo originó) |
| CSV | `csv_import` | `cmr-leads import csv` |
| Descubiertos desde otros perfiles | `discovered_from_*` | Reservado; ver *Limitaciones* |

**Por qué el export y no la API para seguidores/seguidos:** la API oficial de Instagram no permite
listar seguidores ni seguidos (solo da los totales). El export de *Centro de cuentas → Tu información
y permisos → Descargar tu información → Seguidores y seguidos (formato JSON)* sí los trae, es oficial
y no pone en riesgo la cuenta.

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

## Uso

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
cmr-leads status 12 replied

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

## Roadmap

1. **MVP CLI** (este repo): export, CSV, carga manual, Business Discovery, sitio web, scoring,
   revisión, borradores, contacto manual, export y stats.
2. **Descubrimiento local + panel**: fuente Google Places, panel FastAPI + HTMX, login y
   recordatorios de seguimiento.
3. **IA híbrida**: clasificación de casos dudosos y borradores con IA, recalibración del scoring
   con resultados reales.
4. **Escala**: PostgreSQL, múltiples workspaces y roles, WhatsApp Cloud API solo con consentimiento,
   integraciones.
