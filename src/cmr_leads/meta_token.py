"""Estado del token de Meta: errores en castellano y aviso antes de que venza.

Meta responde en inglés ("Error validating access token: Session has expired on…"). Acá se
traduce a algo que se entienda y se consulta /debug_token para saber cuándo vence, así la app
avisa 7 días antes en vez de enterarse cuando ya dejó de andar.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

WARN_DAYS = 7
CACHE_OK_SECONDS = 6 * 3600  # el estado se vuelve a consultar cada 6 horas…
CACHE_ERROR_SECONDS = 30 * 60  # …o a la media hora si Meta no respondió
_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}
_EXPIRED_ON = re.compile(r"expired on \w+, (\d{1,2})-(\w{3})-(\d{2,4})", re.IGNORECASE)


def _expired_date(message: str) -> str | None:
    match = _EXPIRED_ON.search(message or "")
    if not match:
        return None
    day, month, year = match.groups()
    month_n = _MONTHS.get(month.lower())
    if not month_n:
        return None
    year_n = int(year) + (2000 if len(year) == 2 else 0)
    return f"{int(day):02d}/{month_n:02d}/{year_n}"


def explain_auth_error(message: str) -> str:
    """Error de autenticación de Meta (en inglés) → explicación corta en castellano."""
    text = (message or "").lower()
    if date := _expired_date(message):
        return f"El token de Meta venció el {date}. Hay que renovarlo."
    if "expired" in text:
        return "El token de Meta venció. Hay que renovarlo."
    if "changed the password" in text or "session has been invalidated" in text:
        return (
            "El token de Meta se anuló (cambio de contraseña o cierre de sesión en Facebook). "
            "Hay que renovarlo."
        )
    if "not authorized" in text or "has not authorized application" in text:
        return "Se le quitó el permiso a la app en Facebook. Hay que volver a conectarla."
    if "cannot parse" in text or "malformed" in text or "invalid oauth" in text:
        return "El token de Meta no es válido (puede estar mal copiado). Hay que renovarlo."
    return "El token de Meta no es válido o venció. Hay que renovarlo."


@dataclass
class TokenStatus:
    label: str  # "Instagram" / "Publicidad"
    valid: bool | None  # None = no se pudo consultar
    expires_at: datetime | None = None  # None = no vence (o no se sabe)
    problem: str | None = None

    @property
    def days_left(self) -> int | None:
        if not self.expires_at:
            return None
        return (self.expires_at - datetime.now(UTC)).days

    @property
    def expiring_soon(self) -> bool:
        return bool(self.valid and self.days_left is not None and self.days_left < WARN_DAYS)

    @property
    def needs_action(self) -> bool:
        return self.valid is False or self.expiring_soon


def check_token(
    token: str, version: str, label: str, http: httpx.Client | None = None
) -> TokenStatus:
    """Consulta /debug_token (el token se revisa a sí mismo; no hace falta la clave de la app)."""
    client = http or httpx.Client(timeout=4)
    try:
        response = client.get(
            f"https://graph.facebook.com/{version}/debug_token",
            params={"input_token": token, "access_token": token},
        )
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return TokenStatus(label, None)
    if "error" in payload:
        return TokenStatus(
            label, False, problem=explain_auth_error(payload["error"].get("message"))
        )
    data = payload.get("data") or {}
    expires = data.get("expires_at") or 0
    expires_at = datetime.fromtimestamp(expires, UTC) if expires else None
    if not data.get("is_valid"):
        message = (data.get("error") or {}).get("message", "")
        return TokenStatus(label, False, expires_at, explain_auth_error(message))
    return TokenStatus(label, True, expires_at)


_cache: dict[str, tuple[float, TokenStatus]] = {}


def token_statuses(settings, force: bool = False) -> list[TokenStatus]:
    """Estado de los tokens configurados (Instagram y, si es otro, Publicidad), con caché."""
    tokens = []
    if settings.meta_access_token:
        tokens.append(("Instagram", settings.meta_access_token, settings.meta_graph_api_version))
    ads = settings.meta_ads_access_token
    if ads and ads != settings.meta_access_token:
        tokens.append(("Publicidad", ads, settings.meta_ads_graph_api_version))
    result = []
    now = time.monotonic()
    for label, token, version in tokens:
        cached = _cache.get(token)
        if cached and not force and now < cached[0]:
            result.append(cached[1])
            continue
        status = check_token(token, version, label)
        ttl = CACHE_ERROR_SECONDS if status.valid is None else CACHE_OK_SECONDS
        _cache[token] = (now + ttl, status)
        result.append(status)
    return result


def clear_cache() -> None:
    _cache.clear()
