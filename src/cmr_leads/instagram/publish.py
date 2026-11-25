"""Publicar en Instagram desde la app (Content Publishing API).

Cómo funciona:
1. El navegador sube cada foto o video directo a Supabase Storage (link público que Meta baja).
   Las fotos se pasan a JPG antes de subir: Meta solo acepta JPG por link.
2. A la hora indicada, una pasada del publicador crea los "contenedores" en Meta.
3. En las pasadas siguientes (una por minuto) se revisa si Meta terminó de procesarlos
   (los videos tardan) y, cuando están, se publica. Así ninguna llamada queda esperando.
4. Al publicar (o cancelar) se borran los archivos de Supabase.

Instagram no deja programar por API: la app publica a la hora indicada. En la nube lo dispara
pg_cron cada minuto; en la compu, un bucle mientras la app está abierta.
"""

from __future__ import annotations

import logging
import mimetypes
import secrets
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
UPLOAD_EXT = {".jpg", ".mp4", ".mov"}  # lo que llega a Supabase (las fotos ya en JPG)
MAX_CAPTION = 2200
PROCESS_MINUTES = 20  # si Meta no terminó de procesar en este tiempo, se marca error
MAX_FILE_BYTES = 50 * 1024 * 1024  # límite del bucket de Supabase (plan gratis)
UPLOAD_PREFIX = "p"
CUT_MESSAGE = "Se cortó mientras se publicaba. Fijate en Instagram si salió antes de reintentar."


class PublishError(Exception):
    pass


def is_video(name: str) -> bool:
    return Path(name).suffix.lower() in VIDEO_EXT


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


def upload_keys(names: list[str]) -> list[str]:
    """Dónde va cada archivo en el bucket: carpeta al azar y número de orden."""
    folder = f"{UPLOAD_PREFIX}/{secrets.token_urlsafe(16)}"
    keys = []
    for n, name in enumerate(names, start=1):
        ext = Path(name).suffix.lower() if is_video(name) else ".jpg"
        keys.append(f"{folder}/{n:02d}{ext}")
    return keys


def valid_key(key: str) -> bool:
    """Solo se aceptan claves con la forma que arma `upload_keys`."""
    parts = key.split("/")
    return (
        len(parts) == 3
        and parts[0] == UPLOAD_PREFIX
        and len(parts[1]) >= 16
        and Path(parts[2]).suffix.lower() in UPLOAD_EXT
        and ".." not in key
    )


class SupabaseStorage:
    """Bucket público de Supabase Storage: los nombres llevan una parte al azar."""

    def __init__(self, url: str, service_key: str, bucket: str, http: httpx.Client | None = None):
        self._base = url.rstrip("/") + "/storage/v1"
        self._bucket = bucket
        self._headers = {"Authorization": f"Bearer {service_key}", "apikey": service_key}
        self._http = http or httpx.Client(timeout=120)

    def public_url(self, key: str) -> str:
        return f"{self._base}/object/public/{self._bucket}/{key}"

    def signed_upload(self, key: str) -> str:
        """Link para que el navegador suba el archivo directo (sin pasar por la app)."""
        response = self._http.post(
            f"{self._base}/object/upload/sign/{self._bucket}/{key}", headers=self._headers
        )
        if response.status_code >= 300:
            raise PublishError(f"Supabase no dio el link de subida ({response.status_code})")
        return self._base + response.json()["url"]

    def exists(self, key: str) -> bool:
        response = self._http.get(
            f"{self._base}/object/info/{self._bucket}/{key}", headers=self._headers
        )
        return response.status_code == 200

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
        return self.public_url(key)

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
            log.warning("No se pudieron borrar los archivos de Supabase")


def _status(client, container_id: str) -> str:
    """FINISHED o IN_PROGRESS; si Meta dio error, lo lanza con el detalle."""
    data = client.get(container_id, fields="status_code,status")
    code = data.get("status_code")
    if code in (None, "FINISHED", "PUBLISHED"):
        return "FINISHED"
    if code in ("ERROR", "EXPIRED"):
        raise PublishError(f"Instagram no pudo procesar el archivo: {data.get('status') or code}")
    return "IN_PROGRESS"


def start_item(
    item: InstagramScheduled, client, ig_id: str, storage: SupabaseStorage, now: datetime
) -> dict:
    """Paso 1: crea los contenedores en Meta (no espera a que los procese)."""
    caption = (item.caption or "")[:MAX_CAPTION]
    keys = list(item.files or [])
    if not keys:
        raise PublishError("La publicación no tiene archivos.")

    def media(key: str) -> dict:
        url = storage.public_url(key)
        return {"video_url": url} if is_video(key) else {"image_url": url}

    state: dict = {"started": now.isoformat(), "children": [], "parent": None}
    if item.kind == "carrusel":
        for key in keys:
            fields = media(key)
            if is_video(key):
                fields["media_type"] = "VIDEO"
            child = client.post(f"{ig_id}/media", is_carousel_item="true", **fields)["id"]
            state["children"].append(child)
    elif item.kind == "reel":
        state["parent"] = client.post(
            f"{ig_id}/media",
            media_type="REELS",
            video_url=storage.public_url(keys[0]),
            caption=caption,
            share_to_feed="true",
        )["id"]
    elif item.kind == "historia":
        state["parent"] = client.post(f"{ig_id}/media", media_type="STORIES", **media(keys[0]))[
            "id"
        ]
    else:
        state["parent"] = client.post(
            f"{ig_id}/media", image_url=storage.public_url(keys[0]), caption=caption
        )["id"]
    return state


def _fail(item: InstagramScheduled, message: str) -> None:
    item.status = "error"
    item.error = message[:500]
    item.containers = None


def _advance(
    session: Session, item: InstagramScheduled, now: datetime, client, ig_id: str
) -> str | None:
    """Paso 2: si Meta terminó de procesar, publica. Devuelve "publicada" o None (sigue)."""
    state = dict(item.containers or {})
    started = datetime.fromisoformat(state["started"])
    if now - started > timedelta(minutes=PROCESS_MINUTES):
        raise PublishError("Instagram tardó demasiado en procesar el archivo. Probá de nuevo.")
    if state.get("parent") is None:  # carrusel: primero tienen que estar todas las partes
        if any(_status(client, child) != "FINISHED" for child in state["children"]):
            return None
        state["parent"] = client.post(
            f"{ig_id}/media",
            media_type="CAROUSEL",
            children=",".join(state["children"]),
            caption=(item.caption or "")[:MAX_CAPTION],
        )["id"]
        item.containers = state
        session.commit()
        return None
    if _status(client, state["parent"]) != "FINISHED":
        return None
    item.containers = {**state, "publishing": True}
    session.commit()  # antes de publicar: si se corta justo acá, no se publica dos veces
    media_id = client.post(f"{ig_id}/media_publish", creation_id=state["parent"])["id"]
    try:
        permalink = client.get(media_id, fields="permalink").get("permalink")
    except Exception:  # noqa: BLE001 - el link es solo para mostrarlo; ya salió
        permalink = None
    item.status, item.media_id, item.permalink = "publicada", media_id, permalink
    item.published_at, item.error, item.containers = utcnow(), None, None
    session.commit()
    return "publicada"


def run_due(
    session: Session,
    workspace_id: int,
    now: datetime,
    grace_minutes: int,
    client,
    ig_id: str,
    storage: SupabaseStorage,
) -> list[str]:
    """Una pasada: publica lo que Meta terminó de procesar y prepara lo que ya toca.

    Si se pasó mucho la hora sin que nadie la disparara, no publica: queda "vencida" y pregunta.
    Devuelve un resumen de lo que hizo. `now` en UTC.
    """
    done = []
    in_progress = list(
        session.scalars(
            select(InstagramScheduled).where(
                InstagramScheduled.workspace_id == workspace_id,
                InstagramScheduled.status == "publicando",
            )
        )
    )
    for item in in_progress:
        name = f"{KINDS.get(item.kind, item.kind)} {item.id}"
        state = item.containers or {}
        if not state.get("started") or state.get("publishing"):
            # Se cortó justo al publicar: no se reintenta solo, para no publicar dos veces.
            _fail(item, CUT_MESSAGE)
            session.commit()
            done.append(f"{name}: error")
            continue
        try:
            result = _advance(session, item, now, client, ig_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("No se pudo publicar la programación %s", item.id)
            session.rollback()
            _fail(item, str(exc) or exc.__class__.__name__)
            session.commit()
            done.append(f"{name}: error")
            continue
        if result:
            storage.delete(list(item.files or []))
            done.append(f"{name}: publicada")

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
        name = f"{KINDS.get(item.kind, item.kind)} {item.id}"
        if item.publish_at < now - timedelta(minutes=grace_minutes):
            item.status = "vencida"
            item.error = "No se publicó a la hora programada. Elegí si publicarla ahora."
            session.commit()
            done.append(f"{name}: vencida")
            continue
        item.attempts += 1
        try:
            item.containers = start_item(item, client, ig_id, storage, now)
        except Exception as exc:  # noqa: BLE001
            log.exception("No se pudo preparar la programación %s", item.id)
            _fail(item, str(exc) or exc.__class__.__name__)
            session.commit()
            done.append(f"{name}: error")
            continue
        item.status = "publicando"
        session.commit()
        done.append(f"{name}: preparando")
    return done


def cancel(session: Session, item: InstagramScheduled, storage: SupabaseStorage | None) -> None:
    item.status = "cancelada"
    item.containers = None
    if item.plan_item_id:
        plan = session.get(InstagramPlanItem, item.plan_item_id)
        if plan is not None:
            session.delete(plan)
        item.plan_item_id = None
    if storage is not None:
        storage.delete(list(item.files or []))
