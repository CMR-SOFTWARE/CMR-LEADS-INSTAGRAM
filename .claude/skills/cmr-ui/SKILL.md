---
name: cmr-ui
description: Guía visual y de UX de CMR Software Solutions para CMR Lead Finder (y piezas de la marca). Usala SIEMPRE antes de crear o cambiar pantallas, estilos, animaciones, textos de la interfaz, propuestas PDF o mensajes a clientes. Trigger: "diseño", "UI", "estilos", "animación", "pantalla", "propuesta", "colores de CMR", "microcopy".
---

# cmr-ui — Guía de interfaz de CMR

La **guía de estilo de CMR manda**: si cualquier otra fuente (skills, librerías, costumbre)
sugiere otra paleta, tipografía, neón, emojis o estética futurista, gana esta guía.

## 1. Tokens (únicos permitidos)

Todo vive en `src/cmr_leads/web/static/tokens.css`. **Nada de hex sueltos** en otros archivos:
siempre `var(--…)`. Si falta un color, se agrega primero ahí con su contraste medido.

| Token | Valor | Uso |
|---|---|---|
| `--cmr-navy` | #0A1F44 | header, fondos oscuros, títulos sobre claro |
| `--cmr-gris-claro` | #E8ECF2 | fondo claro, texto suave sobre navy |
| `--cmr-azul` | #2E5EFF | acento: botones, números de KPI, links |
| `--cmr-azul-suave` | #6699FF | solo sobre navy, íconos o bordes |
| `--cmr-turquesa` | #1FA59C | 2.º acento: tildes, estados positivos, íconos, pills grandes |
| `--cmr-turquesa-claro` | #2CC4B4 | fin del degradé sobre navy |
| `--cmr-fondo-claro` | #F6F8FB | fondo de la app |
| `--cmr-navy-profundo` / `--cmr-navy-medio` | #081A3A / #0D2752 | degradés oscuros |
| `--cmr-texto-1` / `--cmr-texto-2` | #56688A / #3B4D6E | texto secundario / principal sobre claro |
| `--cmr-texto-sobre-navy` | #C9D3E6 | texto sobre navy |
| `--cmr-fondo-icono` | #EAF0FF | cuadrado detrás de íconos |
| `--cmr-turquesa-texto` | #14806F | **agregado**: turquesa para texto chico y fin del degradé de botones |
| `--cmr-nexo-texto` | #4F46E5 | **agregado**: violeta Nexo para texto chico |
| `--cmr-ok` / `--cmr-alerta` / `--cmr-error` (+ `-suave`) | — | estados (todos AA sobre su fondo) |
| `--nexo-*` | #6366F1 / #A5B4FC / #EEF0FF | badge CMR Nexo (tipografía Inter) |
| `--canchas-*` | #307D24 / #4A973C / #98E484 / #ECF8E8 | badge CMR Canchas (tipografía Rajdhani) |

**Degradés:** `--cmr-degrade` (#2E5EFF → #1FA59C, sobre claro), `--cmr-degrade-navy`
(#6699FF → #2CC4B4, sobre navy), `--cmr-degrade-boton` (versión AA para texto blanco). Se usan
con moderación: 2.ª línea de títulos, botón principal, tildes y detalles.

**Luces difusas** (`--cmr-luces`: azul arriba a la derecha, turquesa abajo a la izquierda): solo en
header, login y estados vacíos. **Nunca detrás de tablas o datos densos.**

**Tipografía:** Poppins 700 títulos (`--tracking-titulo` negativo), 600 botones y pills, 500 texto.
Números de KPI: Poppins 700 en `--cmr-azul` con `font-variant-numeric: tabular-nums`. Cargar solo
los pesos usados (Google Fonts, `display=swap`).

**Radios y sombras:** tarjetas `--radio` (18 px) o `--radio-grande`, sombra `--sombra` (tono navy).
Sobre navy: tarjetas translúcidas con `--borde-navy`.

## 2. Componentes

- **Layout:** header/barra navy con el logo (cubo blanco + "CMR / SOFTWARE SOLUTIONS") arriba a
  la izquierda y luces difusas sutiles. Contenido sobre `--cmr-fondo-claro`, sin luces.
- **Títulos de sección** en 2 líneas cuando aplique: 1.ª en navy, 2.ª con degradé (texto con
  degradé solo en títulos grandes y SIEMPRE con color sólido de respaldo).
- **Tarjetas:** blancas, radio grande, sombra navy suave. Hover: `translateY(-2px)` + sombra.
- **KPIs:** número grande azul tabular, etiqueta corta en `--cmr-texto-1`.
- **Pills/etapas:** MAYÚSCULAS con `--tracking-pill`, 600, fondo suave + texto de su color AA.
  (Esto contradice el consejo genérico de evitar mayúsculas: acá gana la guía de CMR.)
- **Badges de plan:** Nexo violeta (Inter), Canchas verde (Rajdhani), Plan Web / Gestión con la
  identidad CMR.
- **Botones:** píldora. Principal con `--cmr-degrade-boton` y texto blanco; secundario blanco con
  borde. El de WhatsApp lleva el ícono de WhatsApp.
- **Íconos:** de línea (Lucide, ISC), trazo azul o en degradé, dentro de un cuadrado redondeado
  `--cmr-fondo-icono`. **Sin emojis en la interfaz.** Íconos decorativos con `aria-hidden="true"`.
- **Estados vacíos:** tarjeta con luces difusas, ícono de línea y texto con voseo que invite a
  actuar ("Todavía no cargaste leads. Empezá buscando en tu zona."). Sin ilustraciones de stock.
- **Toasts:** cortos, con voseo, `aria-live="polite"`, se van solos.
- **Tablas/listas:** fondo claro liso, hover claro, columnas numéricas tabulares, textos largos
  truncados (`min-width: 0` en flex).
- **Formularios:** `<label>` siempre, `type`/`inputmode` correctos, placeholders que terminan en
  "…" con un ejemplo, errores al lado del campo y diciendo cómo arreglarlo.

## 3. Animación

- Duraciones **150–300 ms** (`--dur-rapida`, `--dur`, `--dur-lenta`), easing suave (`--ease`).
- Animar **solo `transform` y `opacity`**. Nunca `transition: all`: listar propiedades.
- Con **propósito**: feedback de una acción, transición de estado, orientar la vista. Un solo
  momento orquestado por pantalla (ej.: conteo de KPIs al entrar), no efectos en todo.
- Entradas sutiles: `opacity 0→1` + `translateY(8–12px)→0`, escalonado de ~30 ms en listas.
- Conteo de números: anima el valor, con cifras tabulares para que no "salten".
- Skeletons: pulso de opacidad lento; se detiene con movimiento reducido.
- **`prefers-reduced-motion`:** los tokens de duración pasan a 0 y el JS muestra el estado final.
- Nada bloquea la interacción ni dura más de 300 ms. Sin neón, glitch, brillos ni efectos
  "futuristas". Preferí CSS; si hiciera falta, `@formkit/auto-animate` y después Motion.

## 4. Textos (tono)

- Argentino y cercano, con **voseo** ("cargá", "aprobá el lote", "escribinos"). Hablar de
  "el equipo de CMR".
- Botones con la acción concreta ("Guardar propuesta", no "Continuar"); la acción conserva el
  nombre en el toast ("Propuesta guardada").
- "…" en vez de "...", y estados de carga que terminan en "…" ("Buscando…").
- **Todo lo que ve un cliente** (mensajes, plantillas, propuestas PDF):
  - NO mencionar IA. NO dar plazos de entrega.
  - NO poner precios, salvo en la propuesta que aprueba Tomi o promos aprobadas.
  - NO inventar reseñas, clientes ni datos.
  - Contacto oficial: WhatsApp wa.me/543364578599 · Instagram @cmrsoftware.sn ·
    Web cmrsoftwaresolutions.com

## 5. Accesibilidad (checklist)

- [ ] Contraste AA medido (no supuesto). Resultados sobre blanco: navy 16.25, texto-2 8.49,
      texto-1 5.61, azul 5.05, canchas 5.14 → OK para texto. **Turquesa 3.03 y azul-suave 2.78
      NO** para texto chico: usar en íconos, bordes, pills grandes o texto ≥ 18,66 px negrita.
      Blanco sobre turquesa 3.03 → por eso el botón usa `--cmr-degrade-boton`.
- [ ] Foco visible con teclado (`:focus-visible`), nunca `outline: none` sin reemplazo.
- [ ] Botones solo-ícono con `aria-label`; íconos decorativos `aria-hidden`.
- [ ] Jerarquía de encabezados correcta y link "Saltar al contenido".
- [ ] `prefers-reduced-motion` respetado (CSS y JS).
- [ ] Funciona en el celu (360 px) sin scroll horizontal; zoom permitido.
- [ ] Ningún hex fuera de `tokens.css`; ningún emoji en la interfaz.

## 6. Redes (no aplica a la app; para piezas generadas desde el sistema)

| Pieza | Medida | Zona segura (aprox., verificar con la guía vigente de Meta) |
|---|---|---|
| Feed | 1080 × 1350 (4:5) | dejar ~60 px de margen en los bordes |
| Historias | 1080 × 1920 (9:16) | ~250 px libres arriba y abajo para la interfaz de Instagram |
| Reels | 1080 × 1920 (9:16) | ~250 px arriba y ~350 px abajo (descripción y botones) |

Misma identidad: navy o claro con luces difusas, Poppins, título en 2 líneas con degradé, sin
mockups ni ilustraciones de stock.

## Fuentes (solo lectura, auditadas; no quedó nada de terceros instalado)

| Fuente | Repo | Commit revisado | Qué se tomó |
|---|---|---|---|
| frontend-design | anthropics/skills | `ca45fc496a3436251f191354091d557da31c15b5` | criterio de jerarquía, restricción, un solo momento de animación, textos que guían |
| theme-factory | anthropics/skills | `ca45fc496a3436251f191354091d557da31c15b5` | concentrar el tema en tokens (sus paletas NO se usan) |
| web-interface-guidelines | vercel-labs/web-interface-guidelines (vía vercel-labs/agent-skills `1a2c79d4e6c4a2283dc2c614b6538cbe1ed5ca62`) | `434b7f91364665f2f733b310ec54809bf8f37937` | accesibilidad, foco, formularios, animación, tipografía, contenido |
| ui-ux-pro-max | nextlevelbuilder/ui-ux-pro-max-skill | `b7d5353e0130feb8c5574ef1251f8a1db1873ccb` | tiempos de micro-interacciones (hover 150–200 ms, entradas 300 ms, escalonado 30 ms, skeleton). Sus scripts no se ejecutaron |

Descartadas en la auditoría: `web-artifacts-builder` (scripts de instalación, React) y
`taste-skill` (150 scripts, instalador, demasiado grande para auditar).
