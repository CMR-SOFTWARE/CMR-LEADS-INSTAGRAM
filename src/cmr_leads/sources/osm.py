"""Fuente: OpenStreetMap vía Overpass API. Gratis, sin key y sin tarjeta.

Busca comercios por rubro alrededor de cada localidad. En la zona tiene menos teléfonos y webs que
Google, pero no cuesta nada y suma negocios que Google no trae. Respeta la política de uso de
Overpass: una consulta por búsqueda, User-Agent identificado y sin reintentos agresivos.
"""

from collections.abc import Iterator

import httpx

from cmr_leads.domain.enums import SourceType
from cmr_leads.domain.schemas import RawProfile, SourceRecord
from cmr_leads.pipeline.normalize import normalize_text, normalize_username, url_domain

# Servidor principal y un espejo público: si uno está saturado se prueba el otro.
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
BUSY_STATUSES = (429, 502, 503, 504)
USER_AGENT = "CMR-Lead-Finder/1.0 (prospeccion local; uso manual)"

# Centro aproximado de cada localidad y radio de búsqueda en metros.
AREAS = {
    "san nicolas": (-33.3335, -60.2252, 6000),
    "ramallo": (-33.4856, -60.0063, 3000),
    "villa ramallo": (-33.5016, -60.0644, 3000),
    "villa constitucion": (-33.2297, -60.3343, 4000),
    "san pedro": (-33.6795, -59.6666, 4000),
    "pergamino": (-33.8900, -60.5736, 6000),
    "arroyo seco": (-33.1547, -60.5086, 3000),
    "rosario": (-32.9468, -60.6393, 12000),
    "baradero": (-33.8106, -59.5047, 3000),
    "zarate": (-34.0981, -59.0286, 5000),
}

# Rubro (como se escribe en el panel) -> filtros de OSM.
RUBRO_TAGS: dict[str, list[str]] = {
    "peluqueria": ['shop="hairdresser"'],
    "barberia": ['shop="hairdresser"', 'shop="barber"'],
    "centro de estetica": ['shop="beauty"', 'leisure="spa"'],
    "unas": ['shop="beauty"'],
    "spa": ['leisure="spa"', 'shop="beauty"'],
    "masajes": ['shop="massage"'],
    "gimnasio": ['leisure="fitness_centre"'],
    "pilates": ['leisure="fitness_centre"', 'sport="pilates"'],
    "crossfit": ['leisure="fitness_centre"', 'sport="crossfit"'],
    "cancha de padel": ['sport="padel"'],
    "cancha de futbol 5": ['leisure="pitch"]["sport"="soccer"'],
    "complejo deportivo": ['leisure="sports_centre"'],
    "tenis": ['sport="tennis"'],
    "consultorio odontologico": ['amenity="dentist"', 'healthcare="dentist"'],
    "kinesiologia": ['healthcare="physiotherapist"'],
    "psicologo": ['healthcare="psychotherapist"', 'healthcare="psychologist"'],
    "nutricionista": ['healthcare="nutrition_counselling"'],
    "veterinaria": ['amenity="veterinary"'],
    "restaurante": ['amenity="restaurant"'],
    "pizzeria": [
        'amenity="restaurant"]["cuisine"~"pizza"',
        'amenity="fast_food"]["cuisine"~"pizza"',
    ],
    "hamburgueseria": ['amenity="fast_food"]["cuisine"~"burger"'],
    "rotiseria": ['shop="deli"', 'amenity="fast_food"'],
    "panaderia": ['shop="bakery"'],
    "pasteleria": ['shop="pastry"', 'shop="confectionery"'],
    "heladeria": ['amenity="ice_cream"', 'shop="ice_cream"'],
    "cafeteria": ['amenity="cafe"'],
    "tienda de ropa": ['shop="clothes"'],
    "zapateria": ['shop="shoes"'],
    "ferreteria": ['shop="hardware"', 'shop="doityourself"'],
    "optica": ['shop="optician"'],
    "libreria": ['shop="books"', 'shop="stationery"'],
    "muebleria": ['shop="furniture"'],
    "vinoteca": ['shop="wine"', 'shop="alcohol"'],
    "dietetica": ['shop="health_food"', 'shop="nutrition_supplements"'],
    "inmobiliaria": ['office="estate_agent"'],
    "taller mecanico": ['shop="car_repair"'],
    "lavadero de autos": ['amenity="car_wash"'],
    "concesionaria": ['shop="car"'],
    "estudio contable": ['office="accountant"'],
    "estudio juridico": ['office="lawyer"'],
    "escuela de idiomas": ['amenity="language_school"'],
    "fotografo": ['shop="photo"', 'craft="photographer"'],
    "imprenta": ['shop="copyshop"', 'craft="printer"'],
    "salon de eventos": ['amenity="events_venue"'],
}


class OsmError(Exception):
    pass


def resolve_area(area: str) -> tuple[float, float, int] | None:
    key = normalize_text(area.split(",")[0])
    key = key.replace("de los arroyos", "").strip()
    return AREAS.get(key)


def build_query(rubro: str, lat: float, lon: float, radius: int) -> str | None:
    filters = RUBRO_TAGS.get(normalize_text(rubro))
    if not filters:
        return None
    parts = [f'nwr[{f}]["name"](around:{radius},{lat},{lon});' for f in filters]
    return f"[out:json][timeout:25];({''.join(parts)});out center tags 100;"


def _address(tags: dict) -> str | None:
    street, number = tags.get("addr:street"), tags.get("addr:housenumber")
    if not street:
        return None
    city = tags.get("addr:city")
    return ", ".join(p for p in (f"{street} {number or ''}".strip(), city) if p)


def element_to_profile(element: dict, area: str) -> RawProfile:
    tags = element.get("tags") or {}
    website = tags.get("website") or tags.get("contact:website") or tags.get("url")
    instagram = tags.get("contact:instagram") or tags.get("instagram")
    if website and url_domain(website) in ("instagram.com", "m.instagram.com"):
        instagram, website = instagram or website, None
    center = element.get("center") or element
    return RawProfile(
        display_name=tags.get("name"),
        category=tags.get("shop")
        or tags.get("amenity")
        or tags.get("leisure")
        or tags.get("office")
        or tags.get("healthcare")
        or tags.get("craft"),
        phone=tags.get("phone") or tags.get("contact:phone"),
        whatsapp=tags.get("contact:whatsapp"),
        email=tags.get("email") or tags.get("contact:email"),
        website=website,
        instagram_username=normalize_username(instagram) if instagram else None,
        address=_address(tags),
        city=tags.get("addr:city") or area.split(",")[0].strip(),
        country="AR",
        latitude=center.get("lat"),
        longitude=center.get("lon"),
        raw={"osm": {"id": f"{element.get('type')}/{element.get('id')}"}},
    )


class OsmSource:
    name = "osm"

    def __init__(self, rubro: str, area: str, http: httpx.Client | None = None) -> None:
        self.rubro = rubro.strip()
        self.area = area.strip()
        self.http = http or httpx.Client(timeout=40, headers={"User-Agent": USER_AGENT})

    def discover(self) -> Iterator[SourceRecord]:
        place = resolve_area(self.area)
        if place is None:
            raise OsmError(f"No conozco la localidad '{self.area}' en OpenStreetMap")
        query = build_query(self.rubro, *place)
        if query is None:
            raise OsmError(f"El rubro '{self.rubro}' no tiene equivalente en OpenStreetMap")
        payload = None
        for url in OVERPASS_URLS:
            try:
                response = self.http.post(url, data={"data": query})
            except httpx.HTTPError:
                continue
            if response.status_code in BUSY_STATUSES:
                continue
            if response.status_code >= 400:
                raise OsmError(f"OpenStreetMap respondió {response.status_code}")
            try:
                payload = response.json()
            except ValueError:
                continue
            break
        if payload is None:
            raise OsmError("OpenStreetMap está saturado en este momento: probá de nuevo en un rato")
        for element in payload.get("elements", []):
            if not (element.get("tags") or {}).get("name"):
                continue
            yield SourceRecord(
                profile=element_to_profile(element, self.area),
                source=SourceType.OSM,
                source_ref=self.rubro,
            )
