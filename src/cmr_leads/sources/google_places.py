"""Fuente: Google Places API (New) — Text Search.

Busca negocios por rubro y zona ("peluquería en San Nicolás de los Arroyos") con la API oficial
de Google. Cada página trae hasta 20 lugares; Google permite como máximo 3 páginas (60) por
búsqueda. Cada página es una request con costo: se controla con un tope diario.
"""

from collections.abc import Iterator
from typing import Any

import httpx

from cmr_leads.domain.enums import SourceType
from cmr_leads.domain.schemas import RawProfile, SourceRecord
from cmr_leads.pipeline.normalize import normalize_username, url_domain

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
# Place Details pidiendo solo la ubicación: SKU "Essentials" (10.000 gratis por mes).
DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"
FIELDS = (
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.addressComponents",
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "places.websiteUri",
    "places.googleMapsUri",
    "places.primaryTypeDisplayName",
    "places.businessStatus",
    "places.rating",
    "places.userRatingCount",
    "nextPageToken",
)
PAGE_SIZE = 20
MAX_RESULTS = 60

# Rubros sugeridos en el panel (se pueden escribir otros).
SUGGESTED_QUERIES = (
    "peluquería",
    "barbería",
    "centro de estética",
    "gimnasio",
    "consultorio odontológico",
    "kinesiología",
    "veterinaria",
    "restaurante",
    "rotisería",
    "panadería",
    "cafetería",
    "tienda de ropa",
    "ferretería",
    "inmobiliaria",
    "taller mecánico",
    "estudio contable",
    "estudio jurídico",
    "cancha de pádel",
    "salón de eventos",
    "dietética",
    "masajes",
    "psicólogo",
    "nutricionista",
    "uñas",
    "spa",
    "pilates",
    "crossfit",
    "complejo deportivo",
    "cancha de fútbol 5",
    "tenis",
    "pizzería",
    "hamburguesería",
    "heladería",
    "pastelería",
    "zapatería",
    "óptica",
    "librería",
    "mueblería",
    "vinoteca",
    "lavadero de autos",
    "concesionaria",
    "escuela de idiomas",
    "fotógrafo",
    "imprenta",
)

# Localidades sugeridas (zona de influencia de San Nicolás).
SUGGESTED_AREAS = (
    "San Nicolás de los Arroyos, Buenos Aires",
    "Ramallo, Buenos Aires",
    "Villa Ramallo, Buenos Aires",
    "San Pedro, Buenos Aires",
    "Pergamino, Buenos Aires",
    "Villa Constitución, Santa Fe",
    "Arroyo Seco, Santa Fe",
    "Rosario, Santa Fe",
    "Baradero, Buenos Aires",
    "Zárate, Buenos Aires",
)


class PlacesApiError(Exception):
    pass


class GooglePlacesClient:
    def __init__(self, api_key: str, http: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._http = http or httpx.Client(timeout=20)

    def search_text(
        self, query: str, page_token: str | None = None
    ) -> tuple[list[dict], str | None]:
        body: dict[str, Any] = {
            "textQuery": query,
            "languageCode": "es",
            "regionCode": "AR",
            "pageSize": PAGE_SIZE,
        }
        if page_token:
            body["pageToken"] = page_token
        response = self._http.post(
            SEARCH_URL,
            json=body,
            headers={"X-Goog-Api-Key": self._api_key, "X-Goog-FieldMask": ",".join(FIELDS)},
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise PlacesApiError(f"Respuesta inválida de Google ({response.status_code})") from exc
        if response.status_code >= 400 or "error" in payload:
            message = (payload.get("error") or {}).get("message", f"HTTP {response.status_code}")
            raise PlacesApiError(message)
        return payload.get("places", []), payload.get("nextPageToken")

    def geocode(self, address: str) -> tuple[float, float, str] | None:
        """Ubicación de una dirección o lugar escrito a mano: (lat, lng, dirección de Google)."""
        places, _ = self.search_text(address)
        for place in places:
            location = place.get("location") or {}
            if "latitude" in location:
                label = place.get("formattedAddress") or address
                return location["latitude"], location["longitude"], label
        return None

    def get_location(self, place_id: str) -> tuple[float, float] | None:
        response = self._http.get(
            DETAILS_URL.format(place_id=place_id),
            headers={"X-Goog-Api-Key": self._api_key, "X-Goog-FieldMask": "location"},
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise PlacesApiError(f"Respuesta inválida de Google ({response.status_code})") from exc
        if response.status_code >= 400 or "error" in payload:
            message = (payload.get("error") or {}).get("message", f"HTTP {response.status_code}")
            raise PlacesApiError(message)
        location = payload.get("location") or {}
        if "latitude" not in location:
            return None
        return location["latitude"], location["longitude"]


def _component(place: dict, kind: str) -> str | None:
    for component in place.get("addressComponents") or []:
        if kind in (component.get("types") or []):
            return component.get("longText")
    return None


def place_to_profile(place: dict) -> RawProfile:
    website = place.get("websiteUri")
    instagram = None
    # Muchos negocios ponen su Instagram como "sitio web" en Google.
    if website and url_domain(website) in ("instagram.com", "m.instagram.com"):
        instagram = normalize_username(website)
    return RawProfile(
        google_place_id=place.get("id"),
        instagram_username=instagram,
        display_name=(place.get("displayName") or {}).get("text"),
        category=(place.get("primaryTypeDisplayName") or {}).get("text"),
        phone=place.get("internationalPhoneNumber") or place.get("nationalPhoneNumber"),
        website=None if instagram else website,
        address=place.get("formattedAddress"),
        latitude=(place.get("location") or {}).get("latitude"),
        longitude=(place.get("location") or {}).get("longitude"),
        city=_component(place, "locality"),
        province=_component(place, "administrative_area_level_1"),
        country="AR",
        raw={
            "google_places": {
                "maps_url": place.get("googleMapsUri"),
                "rating": place.get("rating"),
                "reviews": place.get("userRatingCount"),
                "business_status": place.get("businessStatus"),
            }
        },
    )


class GooglePlacesSource:
    name = "google_places"

    def __init__(
        self,
        client: GooglePlacesClient,
        query: str,
        area: str,
        max_results: int = MAX_RESULTS,
        max_requests: int = 3,
    ) -> None:
        self.client = client
        self.query = query.strip()
        self.area = area.strip()
        self.max_results = min(max_results, MAX_RESULTS)
        self.max_requests = max_requests
        self.requests_made = 0
        self.skipped_closed = 0

    @property
    def text_query(self) -> str:
        return f"{self.query} en {self.area}" if self.area else self.query

    def discover(self) -> Iterator[SourceRecord]:
        page_token: str | None = None
        yielded = 0
        while yielded < self.max_results and self.requests_made < self.max_requests:
            places, page_token = self.client.search_text(self.text_query, page_token)
            self.requests_made += 1
            for place in places:
                if place.get("businessStatus") == "CLOSED_PERMANENTLY":
                    self.skipped_closed += 1
                    continue
                yield SourceRecord(
                    profile=place_to_profile(place),
                    source=SourceType.GOOGLE_PLACES,
                    source_ref=self.query,
                )
                yielded += 1
                if yielded >= self.max_results:
                    return
            if not page_token:
                return
