"""Publicar en Instagram desde la app (Content Publishing API).

Cómo funciona Meta:
1. Cada foto o video tiene que estar en un link público: Meta lo baja desde ahí.
   Por eso se sube primero a Supabase Storage y se borra cuando termina.
2. Se crea un "contenedor" por archivo (y uno más para el carrusel) y se espera a que
   Meta termine de procesarlo (los videos tardan).
3. Se publica el contenedor y se guarda el ID para que las estadísticas lo sigan.

Instagram no deja programar por API: la app publica a la hora indicada mientras está abierta.
"""

from __future__ import annotations

import io
import logging
import mimetypes
import secrets
import shutil
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from cmr_leads.db.models import InstagramPlanItem, InstagramScheduled, utcnow

log = logging.getLogger(__name__)

KINDS = {"reel": "Reel", "carrusel": "Carrusel", "imagen": "Imagen", "historia": "Historia"}
VIDEO_EXT = {".mp4", ".mov"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_CAPTION = 2200
STUCK_MINUTES = 20


class PublishError(Exception):
    pass


def is_video(name: str) -> bool:
    return Path(name).suffix.lower() in VIDEO_EXT


MAX_FILE_BYTES = 50 * 1024 * 1024  # límite del bucket de Supabase (plan gratis)


def validate_files(kind: str, names: list[str]) -> str | None:
    """Devuelve el error (en criollo) o None si los archivos sirven para ese formato."""
    if not names:
        return "Subí al menos un archivo."
    bad = [n for n in names if Path(n).suffix.lower() not in VIDEO_EXT | IMAGE_EXT]
    if bad:
        return f"Formato no soportado: {bad[0]}. Usá JPG, PNG o WEBP para fotos y MP4 o MOV para videos."
    videos = [n for n in names if is_video(n)]
    if kind == "reel" and (len(names) != 1 or not videos):
        return "Un reel lleva un solo video (MP4 o MOV)."
    if kind == "imagen" and (len(names) != 1 or videos):
        return "Una imagen lleva una sola foto."
    if kind == "historia" and len(names) != 1:
        return "Una historia lleva una sola foto o video."
    if kind == "carrusel" and not 2 <= len(names) <= 10:
        return "Un carrusel lleva entre 2 y 10 fotos o videos."
    return None


def store_files(folder: Path, uploads: list[tuple[str, bytes]]) -> list[str]:
    """Guarda los archivos en la carpeta de la programación. Las fotos se pasan a JPG
    (Meta solo acepta JPG por link) con el ancho máximo que usa Instagram."""
    from PIL import Image, ImageOps

    folder.mkdir(parents=True, exist_ok=True)
    names = []
    for n, (original, raw) in enumerate(uploads, start=1):
        if is_video(original):
            name = f"{n:02d}{Path(original).suffix.lower()}"
            (folder / name).write_bytes(raw)
        else:
            name = f"{n:02d}.jpg"
            image = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
            if image.width > 1440:
                image = image.resize((1440, round(image.height * 1440 / image.width)))
            image.save(folder / name, "JPEG", quality=92)
        names.append(name)
    return names


class SupabaseStorage:
    """Bucket público de Supabase Storage: los nombres llevan una parte al azar."""

    def __init__(self, url: str, service_key: str, bucket: str, http: httpx.Client | None = None):
        self._base = url.rstrip("/") + "/storage/v1"
        self._bucket = bucket
        self._headers = {"Authorization": f"Bearer {service_key}", "apikey": service_key}
        self._http = http or httpx.Client(timeout=120)

    def upload(self, path: Path, key: str) -> str:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        response = self._http.post(
            f"{self._base}/object/{self._bucket}/{key}",
            content=path.read_bytes(),
            headers={**self._headers, "Content-Type": content_type, "x-upsert": "true"},
        )
        if response.status_code >= 300:
            raise PublishError(
                f"No se pudo subir el archivo a Supabase ({response.status_code}): "
                f"{response.text[:200]}"
            )
        return f"{self._base}/object/public/{self._bucket}/{key}"

    def delete(self, keys: list[str]) -> None:
        if not keys:
            return
        try:
            self._http.request(
                "DELETE",
                f"{self._base}/object/{self._bucket}",
                json={"prefixes": keys},
                headers=self._headers,
            )
        except httpx.HTTPError:
            log.warning("No se pudieron borrar los archivos temporales de Supabase")


def _wait(client, container_id: str, sleep: Callable[[float], None], timeout: int = 600) -> None:
    """Espera a que Meta termine de procesar el contenedor (los videos tardan)."""
    waited = 0
    while True:
        status = client.get(container_id, fields="status_code,status").get("status_code")
        if status in (None, "FINISHED", "PUBLISHED"):
            return
        if status in ("ERROR", "EXPIRED"):
            detail = client.get(container_id, fields="status").get("status") or status
            raise PublishError(f"Instagram no pudo procesar el archivo: {detail}")
        if waited >= timeout:
            raise PublishError("Instagram tardó demasiado en procesar el video. Probá de nuevo.")
        sleep(5)
        waited += 5


def publish_item(
    item: InstagramScheduled,
    folder: Path,
    client,
    ig_id: str,
    storage: SupabaseStorage,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, str | None]:
    """Sube los archivos, crea los contenedores y publica. Devuelve (media_id, permalink)."""
    prefix = f"{item.id}-{secrets.token_urlsafe(12)}"
    keys = []
    try:
        urls = []
        for name in item.files or []:
            key = f"{prefix}/{name}"
            urls.append((name, storage.upload(folder / name, key)))
            keys.append(key)
        caption = (item.caption or "")[:MAX_CAPTION]

        def media_fields(name: str, url: str) -> dict:
            return {"video_url": url} if is_video(name) else {"image_url": url}

        if item.kind == "carrusel":
            children = []
            for name, url in urls:
                fields = media_fields(name, url)
                if is_video(name):
                    fields["media_type"] = "VIDEO"
                child = client.post(f"{ig_id}/media", is_carousel_item="true", **fields)["id"]
                _wait(client, child, sleep)
                children.append(child)
            container = client.post(
                f"{ig_id}/media",
                media_type="CAROUSEL",
                children=",".join(children),
                caption=caption,
            )["id"]
        elif item.kind == "reel":
            name, url = urls[0]
            container = client.post(
                f"{ig_id}/media", media_type="REELS", video_url=url, caption=caption,
                share_to_feed="true",
            )["id"]  # fmt: skip
        elif item.kind == "historia":
            name, url = urls[0]
            container = client.post(
                f"{ig_id}/media", media_type="STORIES", **media_fields(name, url)
            )["id"]
        else:
            name, url = urls[0]
            container = client.post(f"{ig_id}/media", image_url=url, caption=caption)["id"]
        _wait(client, container, sleep)
        media_id = client.post(f"{ig_id}/media_publish", creation_id=container)["id"]
        try:
            permalink = client.get(media_id, fields="permalink").get("permalink")
        except Exception:  # el link es solo para mostrarlo; la publicación ya salió
            permalink = None
        return media_id, permalink
    finally:
        storage.delete(keys)


def folder_for(base: Path, item: InstagramScheduled) -> Path:
    return base / str(item.id)


def run_due(
    session: Session,
    workspace_id: int,
    now: datetime,
    grace_minutes: int,
    publish: Callable[[InstagramScheduled], tuple[str, str | None]],
) -> list[str]:
    """Publica lo que ya tocaba. Si se pasó mucho la hora (compu apagada), no publica: pregunta.

    Devuelve un resumen de lo que hizo. `now` en UTC.
    """
    done = []
    stuck = session.scalars(
        select(InstagramScheduled).where(
            InstagramScheduled.workspace_id == workspace_id,
            InstagramScheduled.status == "publicando",
        )
    )
    for item in stuck:  # se cortó la app a mitad de publicación
        if item.publish_at < now - timedelta(minutes=STUCK_MINUTES):
            item.status = "error"
            item.error = (
                "Se cortó mientras se publicaba. Fijate en Instagram si salió antes de reintentar."
            )
    session.commit()

    due = list(
        session.scalars(
            select(InstagramScheduled)
            .where(
                InstagramScheduled.workspace_id == workspace_id,
                InstagramScheduled.status == "pendiente",
                InstagramScheduled.publish_at <= now,
            )
            .order_by(InstagramScheduled.publish_at)
        )
    )
    for item in due:
        if item.publish_at < now - timedelta(minutes=grace_minutes):
            item.status = "vencida"
            item.error = "La app estaba cerrada a la hora programada. Elegí si publicarla ahora."
            session.commit()
            done.append(f"{KINDS[item.kind]} {item.id}: vencida")
            continue
        item.status = "publicando"
        item.attempts += 1
        session.commit()  # antes de llamar a Meta: así no se publica dos veces
        try:
            media_id, permalink = publish(item)
        except Exception as exc:
            log.exception("No se pudo publicar la programación %s", item.id)
            item.status = "error"
            item.error = str(exc)[:500] or exc.__class__.__name__
            session.commit()
            done.append(f"{KINDS[item.kind]} {item.id}: error")
            continue
        item.status = "publicada"
        item.media_id = media_id
        item.permalink = permalink
        item.published_at = utcnow()
        item.error = None
        session.commit()
        done.append(f"{KINDS[item.kind]} {item.id}: publicada")
    return done


def cancel(session: Session, item: InstagramScheduled, base: Path) -> None:
    item.status = "cancelada"
    if item.plan_item_id:
        plan = session.get(InstagramPlanItem, item.plan_item_id)
        if plan is not None:
            session.delete(plan)
        item.plan_item_id = None
    shutil.rmtree(folder_for(base, item), ignore_errors=True)
