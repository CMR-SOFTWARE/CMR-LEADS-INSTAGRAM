"""Cliente mínimo de la Instagram Graph API (login con Facebook) para la cuenta propia de CMR."""

from collections.abc import Callable
from typing import Any

import httpx

from cmr_leads.enrichment.instagram_discovery import (
    AUTH_ERROR_CODES,
    GRAPH_URL,
    RATE_LIMIT_CODES,
    AuthError,
    InstagramApiError,
    RateLimitedError,
)


class GraphClient:
    def __init__(
        self,
        access_token: str,
        api_version: str = "v23.0",
        http: httpx.Client | None = None,
        on_request: Callable[[], None] | None = None,
    ) -> None:
        self._token = access_token
        self._version = api_version
        self._http = http or httpx.Client(timeout=20)
        self._on_request = on_request
        self.requests_made = 0

    def get(self, path: str, token: str | None = None, **params: Any) -> dict:
        return self._request("GET", path, token, params)

    def post(self, path: str, token: str | None = None, **data: Any) -> dict:
        return self._request("POST", path, token, data)

    def _request(self, method: str, path: str, token: str | None, params: dict) -> dict:
        if self._on_request:
            self._on_request()  # puede cortar por tope diario
        self.requests_made += 1
        params["access_token"] = token or self._token
        url = f"{GRAPH_URL}/{self._version}/{path.lstrip('/')}"
        if method == "GET":
            response = self._http.get(url, params=params)
        else:
            response = self._http.post(url, data=params)
        try:
            payload = response.json()
        except ValueError as exc:
            raise InstagramApiError(f"Respuesta no JSON ({response.status_code})") from exc
        if "error" in payload:
            error = payload["error"]
            code, message = error.get("code"), error.get("message", "")
            if code in RATE_LIMIT_CODES:
                raise RateLimitedError(message)
            if code in AUTH_ERROR_CODES:
                raise AuthError(message)
            raise InstagramApiError(f"[{code}/{error.get('error_subcode')}] {message}")
        return payload


def metric_values(payload: dict) -> dict[str, int]:
    """Convierte la respuesta de /insights en {metrica: valor}."""
    values: dict[str, int] = {}
    for item in payload.get("data", []):
        name = item.get("name")
        if "total_value" in item:
            values[name] = int((item["total_value"] or {}).get("value") or 0)
        elif item.get("values"):
            values[name] = sum(
                int(v.get("value") or 0)
                for v in item["values"]
                if isinstance(v.get("value"), int | float)
            )
    return values
