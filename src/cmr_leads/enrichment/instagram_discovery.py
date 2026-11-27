"""Cliente de la Business Discovery API (Instagram Graph API, oficial).

Devuelve datos públicos de cuentas profesionales (Business/Creator) a partir del username.
Las cuentas personales no son consultables: la API responde error y lo tratamos como `None`.
"""

import httpx

from cmr_leads.domain.schemas import RawProfile
from cmr_leads.pipeline.normalize import normalize_username

GRAPH_URL = "https://graph.facebook.com"
FIELDS = "id,username,name,biography,website,followers_count,follows_count,media_count"

# Códigos de error de la Graph API (ver documentación vigente de Meta).
RATE_LIMIT_CODES = {4, 17, 32, 613, 80002}
AUTH_ERROR_CODES = {102, 190}
NOT_FOUND_CODES = {110}
NOT_FOUND_SUBCODES = {2207013}


class InstagramApiError(Exception):
    pass


class RateLimitedError(InstagramApiError):
    pass


class AuthError(InstagramApiError):
    pass


class BusinessDiscoveryClient:
    def __init__(
        self,
        access_token: str,
        ig_user_id: str,
        api_version: str = "v23.0",
        http: httpx.Client | None = None,
    ) -> None:
        self._token = access_token
        self._ig_user_id = ig_user_id
        self._api_version = api_version
        self._http = http or httpx.Client(timeout=15)

    def lookup(self, username: str) -> RawProfile | None:
        clean = normalize_username(username)
        if not clean:
            raise ValueError(f"Username inválido: {username!r}")
        response = self._http.get(
            f"{GRAPH_URL}/{self._api_version}/{self._ig_user_id}",
            params={
                "fields": f"business_discovery.username({clean}){{{FIELDS}}}",
                "access_token": self._token,
            },
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise InstagramApiError(f"Respuesta no JSON ({response.status_code})") from exc

        if "error" in payload:
            error = payload["error"]
            code, subcode = error.get("code"), error.get("error_subcode")
            message = error.get("message", "")
            if code in RATE_LIMIT_CODES:
                raise RateLimitedError(message)
            if code in AUTH_ERROR_CODES:
                from cmr_leads.meta_token import explain_auth_error

                raise AuthError(explain_auth_error(message))
            if code in NOT_FOUND_CODES or subcode in NOT_FOUND_SUBCODES:
                return None
            raise InstagramApiError(f"[{code}/{subcode}] {message}")

        data = payload.get("business_discovery")
        if not data:
            return None
        return RawProfile(
            instagram_user_id=data.get("id"),
            instagram_username=data.get("username"),
            display_name=data.get("name"),
            bio=data.get("biography"),
            website=data.get("website"),
            followers=data.get("followers_count"),
            following=data.get("follows_count"),
            posts_count=data.get("media_count"),
            is_business_account=True,
            raw={"business_discovery": data},
        )
