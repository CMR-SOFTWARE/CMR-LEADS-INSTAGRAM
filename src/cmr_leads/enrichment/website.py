"""Análisis liviano y respetuoso del sitio público de un lead: 1 request a la home.

Respeta robots.txt, se identifica con un User-Agent honesto y no sigue más páginas.
"""

import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

USER_AGENT = "CMRLeadFinder/0.1 (+prospeccion comercial; CMR Software Solutions)"
HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
VIEWPORT_RE = re.compile(r"""<meta[^>]+name\s*=\s*["']viewport["']""", re.I)
TAG_RE = re.compile(r"<[^>]+>")
ECOMMERCE_HINTS = (
    "tiendanube",
    "mitiendanube",
    "woocommerce",
    "shopify",
    "mercadoshops",
    "add-to-cart",
    "carrito",
)


@dataclass
class WebsiteSignals:
    url: str
    reachable: bool = False
    skipped_reason: str | None = None
    final_url: str | None = None
    https: bool | None = None
    mobile_viewport: bool | None = None
    title: str | None = None
    ecommerce: bool | None = None
    links: list[str] = field(default_factory=list)
    text: str = ""

    def to_json(self) -> dict:
        data = asdict(self)
        data.pop("text")
        data["links"] = self.links[:50]
        return data


def _allowed_by_robots(client: httpx.Client, url: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response = client.get(robots_url)
    except httpx.HTTPError:
        return True
    if response.status_code >= 400:
        return True
    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


def fetch_website_signals(
    url: str, timeout: float = 10.0, client: httpx.Client | None = None
) -> WebsiteSignals:
    signals = WebsiteSignals(url=url)
    owns_client = client is None
    client = client or httpx.Client(
        timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )
    try:
        if not _allowed_by_robots(client, url):
            signals.skipped_reason = "robots.txt no lo permite"
            return signals
        response = client.get(url)
    except httpx.HTTPError as exc:
        signals.skipped_reason = f"no responde ({type(exc).__name__})"
        return signals
    finally:
        if owns_client:
            client.close()

    signals.reachable = response.status_code < 400
    signals.final_url = str(response.url)
    signals.https = response.url.scheme == "https"
    if not signals.reachable:
        signals.skipped_reason = f"HTTP {response.status_code}"
        return signals
    html = response.text[:500_000]
    signals.mobile_viewport = bool(VIEWPORT_RE.search(html))
    if match := TITLE_RE.search(html):
        signals.title = re.sub(r"\s+", " ", match.group(1)).strip()[:200]
    lowered = html.lower()
    signals.ecommerce = any(hint in lowered for hint in ECOMMERCE_HINTS)
    signals.links = list(
        dict.fromkeys(urljoin(str(response.url), h) for h in HREF_RE.findall(html))
    )
    signals.text = re.sub(r"\s+", " ", TAG_RE.sub(" ", html))[:20_000]
    return signals
