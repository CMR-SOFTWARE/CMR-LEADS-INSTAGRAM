"""Servir la app debajo de una ruta (ej: cmrsoftwaresolutions.com/leads).

La landing reenvía /leads/... a esta app. Este middleware:
- Al entrar, saca el prefijo (/leads/instagram → /instagram), así las rutas no cambian.
  Sin prefijo también funciona (la dirección de Vercel directa).
- Al salir, se lo agrega a los enlaces del HTML (href, src, action, data-*) y a las
  redirecciones, para que la navegación siga dentro de /leads.
Con BASE_PATH vacío (en la compu) no hace nada.
"""

from __future__ import annotations

import re

LINK = re.compile(rb'((?:href|src|action|formaction|data-[a-z-]+)=")/(?!/)')


def _prefix_location(value: str, base: str) -> str:
    # Siempre se agrega: la app nunca arma rutas con el prefijo, y "/leads/2" es una ruta
    # propia de la app (la ficha del lead), no una que ya lo traiga.
    if value.startswith("/") and not value.startswith("//"):
        return base + value
    return value


class BasePathMiddleware:
    def __init__(self, app, base: str):
        self.app = app
        self.base = "/" + base.strip("/") if base.strip("/") else ""

    async def __call__(self, scope, receive, send):
        if not self.base or scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path == self.base or path.startswith(self.base + "/"):
            scope = dict(scope, path=path[len(self.base) :] or "/")
            scope["raw_path"] = scope["path"].encode()
        base, base_b = self.base, self.base.encode()
        state: dict = {}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = []
                for k, v in message.get("headers", []):
                    if k.lower() == b"location":
                        v = _prefix_location(v.decode("latin-1"), base).encode("latin-1")
                    headers.append((k, v))
                content_type = dict((k.lower(), v) for k, v in headers).get(b"content-type", b"")
                state["html"] = content_type.startswith(b"text/html")
                if state["html"]:
                    state["start"] = dict(message, headers=headers)
                    state["body"] = []
                    return None
                return await send(dict(message, headers=headers))
            if message["type"] == "http.response.body" and state.get("html"):
                state["body"].append(message.get("body", b""))
                if message.get("more_body"):
                    return None
                body = LINK.sub(rb"\1" + base_b + b"/", b"".join(state["body"]))
                start = state["start"]
                start["headers"] = [
                    (k, str(len(body)).encode() if k.lower() == b"content-length" else v)
                    for k, v in start["headers"]
                ]
                await send(start)
                return await send({"type": "http.response.body", "body": body})
            return await send(message)

        return await self.app(scope, receive, send_wrapper)
