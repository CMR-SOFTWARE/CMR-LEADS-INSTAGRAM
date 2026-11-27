"""Hoja de ruta para dejar folletos: de una salida a una llegada, pasando por los negocios con
más chances de contratar que quedan cerca del camino.

1. Candidatos: leads con ubicación, que se pueden contactar y cuyo desvío (ir de la salida al
   local y del local a la llegada, contra ir directo) no supera el máximo.
2. Se eligen los de más chance (análisis de la IA si lo hay; si no, el puntaje).
3. Se ordenan para caminar/manejar lo menos posible (inserción más barata + 2-opt).
Las distancias son en línea recta: alcanzan para elegir y ordenar; el camino real lo arma
Google Maps con el link de cada tramo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from urllib.parse import urlencode

from cmr_leads.db.models import Lead
from cmr_leads.domain.enums import LeadStatus

Point = tuple[float, float]

# Estados en los que no tiene sentido dejarle un folleto.
SKIP_STATUSES = {LeadStatus.WON, LeadStatus.LOST, LeadStatus.REJECTED}
MAX_STOPS = 25
# Google Maps acepta pocas paradas intermedias por link: la ruta se parte en tramos.
WAYPOINTS_PER_LINK = 9
TRAVEL_MODES = {"walking": "A pie", "driving": "En auto", "bicycling": "En bici"}


def distance_km(a: Point, b: Point) -> float:
    """Distancia en línea recta (haversine)."""
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    )
    return 2 * 6371 * math.asin(math.sqrt(h))


def chance_of(lead: Lead) -> int:
    """Chance de contratar (0-100): la de la IA si ya se analizó; si no, el puntaje."""
    offer = (lead.raw_data or {}).get("ai_offer") or {}
    if isinstance(offer.get("probability"), int | float):
        return int(offer["probability"])
    return int(lead.business_score or 0)


def is_candidate(lead: Lead) -> bool:
    return (
        lead.latitude is not None
        and lead.longitude is not None
        and not lead.do_not_contact
        and not lead.excluded
        and lead.status not in SKIP_STATUSES
    )


@dataclass
class Stop:
    lead: Lead
    chance: int
    detour_km: float

    @property
    def point(self) -> Point:
        return (self.lead.latitude, self.lead.longitude)


@dataclass
class RoutePlan:
    start: Point
    end: Point
    mode: str
    stops: list[Stop] = field(default_factory=list)
    candidates: int = 0

    @property
    def points(self) -> list[Point]:
        return [self.start, *(s.point for s in self.stops), self.end]

    @property
    def total_km(self) -> float:
        pts = self.points
        return sum(distance_km(a, b) for a, b in zip(pts, pts[1:], strict=False))

    @property
    def maps_links(self) -> list[str]:
        """Links de Google Maps por tramo (cada uno con hasta 9 paradas intermedias)."""
        pts = self.points
        links = []
        i = 0
        while i < len(pts) - 1:
            leg = pts[i : i + WAYPOINTS_PER_LINK + 2]
            params = {
                "api": "1",
                "origin": _fmt(leg[0]),
                "destination": _fmt(leg[-1]),
                "travelmode": self.mode,
            }
            if len(leg) > 2:
                params["waypoints"] = "|".join(_fmt(p) for p in leg[1:-1])
            links.append("https://www.google.com/maps/dir/?" + urlencode(params))
            i += len(leg) - 1
        return links


def _fmt(p: Point) -> str:
    return f"{p[0]:.6f},{p[1]:.6f}"


def _route_length(points: list[Point]) -> float:
    return sum(distance_km(a, b) for a, b in zip(points, points[1:], strict=False))


def _order(start: Point, end: Point, stops: list[Stop]) -> list[Stop]:
    """Orden con poco recorrido: inserción más barata y después 2-opt."""
    route: list[Stop] = []
    for stop in sorted(stops, key=lambda s: s.detour_km):
        best_i, best_cost = 0, math.inf
        pts = [start, *(s.point for s in route), end]
        for i in range(len(pts) - 1):
            cost = (
                distance_km(pts[i], stop.point)
                + distance_km(stop.point, pts[i + 1])
                - distance_km(pts[i], pts[i + 1])
            )
            if cost < best_cost:
                best_i, best_cost = i, cost
        route.insert(best_i, stop)

    improved = True
    while improved:
        improved = False
        for i in range(len(route) - 1):
            for j in range(i + 1, len(route)):
                candidate = route[:i] + route[i : j + 1][::-1] + route[j + 1 :]
                old = _route_length([start, *(s.point for s in route), end])
                new = _route_length([start, *(s.point for s in candidate), end])
                if new < old - 1e-9:
                    route, improved = candidate, True
    return route


def plan_route(
    leads: list[Lead],
    start: Point,
    end: Point,
    max_stops: int = 10,
    max_detour_km: float = 2.0,
    mode: str = "walking",
    min_chance: int = 0,
) -> RoutePlan:
    max_stops = max(1, min(max_stops, MAX_STOPS))
    direct = distance_km(start, end)
    pool = []
    for lead in leads:
        if not is_candidate(lead):
            continue
        chance = chance_of(lead)
        if chance < min_chance:
            continue
        point = (lead.latitude, lead.longitude)
        detour = distance_km(start, point) + distance_km(point, end) - direct
        if detour <= max_detour_km:
            pool.append(Stop(lead=lead, chance=chance, detour_km=round(detour, 2)))
    pool.sort(key=lambda s: (-s.chance, s.detour_km))
    plan = RoutePlan(start=start, end=end, mode=mode if mode in TRAVEL_MODES else "walking")
    plan.candidates = len(pool)
    plan.stops = _order(start, end, pool[:max_stops])
    return plan
