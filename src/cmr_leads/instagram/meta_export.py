"""Importa los CSV que exporta Meta Business Suite (Estadísticas > Exportar datos).

Sirve sobre todo para las historias: la API de Instagram solo deja leerlas durante 24 h,
pero el export de Meta trae las de los últimos meses con sus números finales.
Las publicaciones del feed ya las trae la API, así que acá solo se agregan si faltan.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmr_leads.db.models import InstagramPost, utcnow

ID = "identificador de la publicación"
TYPE = "Tipo de publicación"

PRODUCT = {
    "Historia de Instagram": ("STORY", None),
    "Reel de Instagram": ("REELS", "VIDEO"),
    "Imagen de Instagram": ("FEED", "IMAGE"),
    "Secuencia de Instagram": ("FEED", "CAROUSEL_ALBUM"),
}

FIELDS = {
    "Visualizaciones": "views",
    "Alcance": "reach",
    "Me gusta": "likes",
    "Veces que se ha compartido": "shares",
    "Comentarios": "comments",
    "Veces guardado": "saves",
    "Respuestas": "replies",
    "Visitas al perfil": "profile_visits",
    "Seguidores": "follows",
}


class MetaExportError(ValueError):
    pass


@dataclass
class ImportResult:
    new_stories: int = 0
    updated_stories: int = 0
    new_posts: int = 0
    completed_posts: int = 0
    skipped: int = 0

    def summary(self) -> str:
        parts = []
        if self.new_stories:
            parts.append(f"{self.new_stories} historias nuevas")
        if self.updated_stories:
            parts.append(f"{self.updated_stories} historias actualizadas")
        if self.new_posts:
            parts.append(f"{self.new_posts} publicaciones nuevas")
        if self.completed_posts:
            parts.append(f"{self.completed_posts} publicaciones completadas")
        if not parts:
            return "El archivo no trajo nada nuevo: ya estaba todo guardado."
        text = "Importado: " + ", ".join(parts) + "."
        if self.skipped:
            text += f" Se saltearon {self.skipped} filas que no eran totales."
        return text


def _pacific_to_utc(local: datetime) -> datetime:
    """Meta exporta la hora en horario del Pacífico (UTC-8, o UTC-7 con horario de verano)."""
    march = date(local.year, 3, 8)
    dst_start = march + timedelta(days=(6 - march.weekday()) % 7)  # segundo domingo de marzo
    november = date(local.year, 11, 1)
    dst_end = november + timedelta(days=(6 - november.weekday()) % 7)  # primer domingo de noviembre
    summer = (
        datetime(*dst_start.timetuple()[:3], 2) <= local < datetime(*dst_end.timetuple()[:3], 2)
    )
    return local + timedelta(hours=7 if summer else 8)


def _number(value) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(float(value.replace(",", "")))
    except ValueError:
        return None


def read_rows(raw: bytes) -> list[dict]:
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or ID not in rows[0] or TYPE not in rows[0]:
        raise MetaExportError(
            "El archivo no parece un export de Meta: falta la columna de identificador o de tipo."
        )
    return rows


def import_meta_export(session: Session, workspace_id: int, raw: bytes) -> ImportResult:
    rows = read_rows(raw)
    existing = {
        p.media_id: p
        for p in session.scalars(
            select(InstagramPost).where(InstagramPost.workspace_id == workspace_id)
        )
    }
    result = ImportResult()
    for row in rows:
        media_id = (row.get(ID) or "").strip()
        kind = PRODUCT.get((row.get(TYPE) or "").strip())
        if not media_id or kind is None or (row.get("Fecha") or "Total").strip() != "Total":
            result.skipped += 1
            continue
        product, media_type = kind
        values = {attr: _number(row.get(col)) for col, attr in FIELDS.items() if col in row}
        post = existing.get(media_id)
        if post is not None:
            if product == "STORY":  # números finales: reemplazan los que se leyeron en vivo
                for attr, value in values.items():
                    if value is not None:
                        setattr(post, attr, value)
                result.updated_stories += 1
            else:  # el feed lo trae la API: solo se completa lo que Meta no da (p. ej. seguidores)
                filled = False
                for attr, value in values.items():
                    if value is not None and getattr(post, attr) is None:
                        setattr(post, attr, value)
                        filled = True
                result.completed_posts += int(filled)
            continue

        published = None
        if row.get("Hora de publicación"):
            try:
                local = datetime.strptime(row["Hora de publicación"].strip(), "%m/%d/%Y %H:%M")
                published = _pacific_to_utc(local)
            except ValueError:
                published = None
        if media_type is None:
            media_type = "VIDEO" if (_number(row.get("Duración (segundos)")) or 0) > 0 else "IMAGE"
        post = InstagramPost(
            workspace_id=workspace_id,
            media_id=media_id,
            media_type=media_type,
            product_type=product,
            caption=(row.get("Descripción") or "")[:2000] or None,
            permalink=row.get("Enlace permanente") or None,
            published_at=published,
            fetched_at=utcnow(),
            **{k: v for k, v in values.items() if v is not None},
        )
        post.total_interactions = sum(
            values.get(k) or 0 for k in ("likes", "comments", "shares", "saves", "replies")
        )
        session.add(post)
        existing[media_id] = post
        if product == "STORY":
            result.new_stories += 1
        else:
            result.new_posts += 1
    session.flush()
    return result


# --- Gráficos diarios (Estadísticas > Resultados > Exportar en cada gráfico) ---

DAILY = {
    "Visualizaciones": "views",
    "Alcance": "reach",
    "Visitas al perfil de Instagram": "profile_views",
    "Interacciones con el contenido": "total_interactions",
    "Clics en el enlace de Instagram": "clicks",
    "Seguidores de Instagram": "follows",
}


def is_daily_export(raw: bytes) -> bool:
    return raw[:2] in (b"\xff\xfe", b"\xfe\xff") or raw.lstrip(b"\xef\xbb\xbf")[:4] == b"sep="


def import_daily_export(session: Session, workspace_id: int, raw: bytes, today: date) -> str:
    """Completa días que falten y corrige los clics en el enlace (la API cuenta menos que Meta).

    No pisa días que ya están guardados (salvo los clics) ni toca el día de hoy, que sigue abierto.
    """
    from cmr_leads.db.models import InstagramDaily

    encoding = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    lines = [line for line in raw.decode(encoding).splitlines() if line.strip()]
    if lines and lines[0].startswith("sep="):
        lines = lines[1:]
    title = lines[0].strip().strip('"') if lines else ""
    attr = DAILY.get(title)
    if attr is None:
        raise MetaExportError(f'No reconozco el gráfico "{title}".')
    values: dict[date, int] = {}
    for row in csv.reader(lines[2:]):
        if len(row) < 2:
            continue
        try:
            values[date.fromisoformat(row[0][:10])] = int(float(row[1]))
        except ValueError:
            continue
    existing = {
        r.day.date(): r
        for r in session.scalars(
            select(InstagramDaily).where(InstagramDaily.workspace_id == workspace_id)
        )
    }
    added = fixed = 0
    for day, value in sorted(values.items()):
        if day >= today:
            continue
        row = existing.get(day)
        if row is None:
            if not value:
                continue
            row = InstagramDaily(
                workspace_id=workspace_id, day=datetime(day.year, day.month, day.day)
            )
            session.add(row)
            existing[day] = row
            added += 1
        if attr == "clicks":
            # Clics en enlaces del perfil e historias: la API los devuelve en cero.
            # Es otro dato que los toques en el botón del sitio web (website_clicks).
            if value != (row.link_taps or 0):
                row.link_taps = value
                fixed += 1
        elif getattr(row, attr) is None:
            setattr(row, attr, value)
            fixed += 1
    session.flush()
    if not added and not fixed:
        return f"{title}: ya estaba todo guardado."
    text = f"{title}: {fixed} días completados"
    if added:
        text += f" ({added} nuevos)"
    return text + "."
