"""Cliente de la Marketing API de Meta (Graph API) con reintentos ante límites de uso."""

import json
import logging
import random
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

GRAPH_URL = "https://graph.facebook.com"
# Límites de uso: se reintenta esperando cada vez más.
RATE_LIMIT_CODES = {4, 17, 32, 613} | set(range(80000, 80015))
# Errores transitorios de Meta ("intentá más tarde").
TRANSIENT_CODES = {1, 2}
AUTH_ERROR_CODES = {102, 190}
PERMISSION_CODES = {10, 200, 294}

log = logging.getLogger(__name__)


class AdsApiError(Exception):
    pass


class AdsRateLimitedError(AdsApiError):
    pass


class AdsAuthError(AdsApiError):
    pass


class AdsClient:
    def __init__(
        self,
        access_token: str,
        api_version: str = "v23.0",
        http: httpx.Client | None = None,
        on_request: Callable[[], None] | None = None,
        max_retries: int = 4,
        base_delay: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = access_token
        self._version = api_version
        self._http = http or httpx.Client(timeout=30)
        self._on_request = on_request
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._sleep = sleep
        self.requests_made = 0

    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{GRAPH_URL}/{self._version}/{path.lstrip('/')}"

    def get(self, path: str, **params: Any) -> dict:
        query = {k: json.dumps(v) if isinstance(v, dict | list) else v for k, v in params.items()}
        if not path.startswith("http"):
            # Las URLs de `paging.next` ya traen el token y los filtros.
            query["access_token"] = self._token
        for attempt in range(self._max_retries + 1):
            if self._on_request:
                self._on_request()  # puede cortar por tope diario
            self.requests_made += 1
            try:
                # Con params={} httpx borraría la query de la URL de paginación.
                response = self._http.get(self._url(path), params=query or None)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise AdsApiError(f"Sin conexión con Meta: {exc}") from exc
                self._wait(attempt)
                continue
            try:
                payload = response.json()
            except ValueError as exc:
                if response.status_code >= 500 and attempt < self._max_retries:
                    self._wait(attempt)
                    continue
                raise AdsApiError(f"Respuesta inesperada de Meta ({response.status_code})") from exc
            error = payload.get("error") if isinstance(payload, dict) else None
            if not error:
                return payload
            code = error.get("code")
            message = error.get("error_user_msg") or error.get("message", "")
            if code in AUTH_ERROR_CODES:
                raise AdsAuthError(f"El token de Meta no es válido o venció: {message}")
            if code in PERMISSION_CODES:
                raise AdsAuthError(f"Al token le faltan permisos (ads_read): {message}")
            retryable = code in RATE_LIMIT_CODES or code in TRANSIENT_CODES
            if retryable and attempt < self._max_retries:
                log.warning("Meta pidió esperar (código %s), reintento %s", code, attempt + 1)
                self._wait(attempt)
                continue
            if code in RATE_LIMIT_CODES:
                raise AdsRateLimitedError(f"Meta limitó las consultas por un rato: {message}")
            raise AdsApiError(f"[{code}/{error.get('error_subcode')}] {message}")
        raise AdsApiError("No se pudo completar la consulta a Meta")

    def get_all(self, path: str, **params: Any) -> Iterator[dict]:
        """Recorre todas las páginas de un listado (`data` + `paging.next`)."""
        payload = self.get(path, **params)
        while True:
            yield from payload.get("data", [])
            next_url = (payload.get("paging") or {}).get("next")
            if not next_url:
                return
            payload = self.get(next_url)

    def _wait(self, attempt: int) -> None:
        # Espera exponencial con un poco de azar para no chocar de nuevo al mismo tiempo.
        delay = self._base_delay * (2**attempt)
        self._sleep(delay + random.uniform(0, delay / 2))
