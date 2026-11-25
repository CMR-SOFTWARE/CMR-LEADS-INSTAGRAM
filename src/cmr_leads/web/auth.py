"""Login con los mismos usuarios y contraseñas del gestor de CMR (tomi, chipi, gena).

El gestor guarda el SHA-256 de cada contraseña. Acá se verifica en el servidor contra esos
mismos hashes y se deja una cookie firmada. Los hashes salen de LOGIN_HASHES
("tomi:abc...,chipi:def...") o, si no está, se leen del gestor publicado: así, si cambian una
contraseña en el gestor, cambia acá también.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time

import httpx

COOKIE = "cmr_sesion"
MAX_AGE = 30 * 24 * 3600  # 30 días
PUBLIC_PREFIXES = ("/login", "/logout", "/static/", "/tareas/", "/favicon")
_HASH = re.compile(r"(\w+)\s*:\s*['\"]([0-9a-f]{64})['\"]")
_cache: dict = {"at": 0.0, "hashes": {}}


def parse_hashes(text: str) -> dict[str, str]:
    """Acepta "usuario:hash,usuario:hash" o el bloque `const HASHES = {...}` del gestor."""
    if not text:
        return {}
    if "HASHES" in text:
        text = text[text.index("HASHES") :]
        text = text[: text.index("}") + 1] if "}" in text else text
        return {u.lower(): h for u, h in _HASH.findall(text)}
    pairs = (part.split(":", 1) for part in text.split(",") if ":" in part)
    return {u.strip().lower(): h.strip().lower() for u, h in pairs if len(h.strip()) == 64}


def load_hashes(configured: str | None, gestor_url: str | None, ttl: int = 600) -> dict[str, str]:
    if configured:
        return parse_hashes(configured)
    if not gestor_url:
        return {}
    if time.monotonic() - _cache["at"] < ttl and _cache["hashes"]:
        return _cache["hashes"]
    try:
        response = httpx.get(gestor_url, timeout=10)
        response.raise_for_status()
        hashes = parse_hashes(response.text)
    except httpx.HTTPError:
        return _cache["hashes"]  # si el gestor no responde, sigue lo último que se leyó
    if hashes:
        _cache.update(at=time.monotonic(), hashes=hashes)
    return hashes


def check_password(hashes: dict[str, str], user: str, password: str) -> str | None:
    """Devuelve el usuario si la contraseña es correcta."""
    user = (user or "").strip().lower()
    expected = hashes.get(user)
    if not expected or not password:
        return None
    given = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return user if hmac.compare_digest(given, expected) else None


def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_cookie(secret: str, user: str, now: float | None = None) -> str:
    expires = int((now or time.time()) + MAX_AGE)
    payload = f"{user}|{expires}"
    return f"{payload}|{_sign(secret, payload)}"


def read_cookie(secret: str, value: str | None, now: float | None = None) -> str | None:
    """El usuario de la cookie si la firma es válida y no venció."""
    try:
        user, expires, signature = (value or "").split("|")
        if not hmac.compare_digest(signature, _sign(secret, f"{user}|{expires}")):
            return None
        return user if int(expires) > (now or time.time()) else None
    except ValueError:
        return None


def is_public(path: str) -> bool:
    return path == "/healthz" or path.startswith(PUBLIC_PREFIXES)
