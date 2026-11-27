from types import SimpleNamespace

from cmr_leads.domain.enums import LeadStatus
from cmr_leads.sales.route import distance_km, plan_route

START = (-33.3350, -60.2200)  # centro de San Nicolás (aprox.)
END = (-33.3350, -60.2000)  # ~1,9 km al este


def _lead(id, lat, lng, score=50, **extra):
    data = dict(
        id=id,
        latitude=lat,
        longitude=lng,
        business_score=score,
        raw_data={},
        do_not_contact=False,
        excluded=False,
        status=LeadStatus.APPROVED,
    )
    data.update(extra)
    return SimpleNamespace(**data)


def test_distance_is_reasonable():
    assert 1.7 < distance_km(START, END) < 2.0


def test_picks_highest_chance_near_the_path_and_orders_them():
    leads = [
        _lead(1, -33.3350, -60.2050, score=40),  # cerca del final
        _lead(2, -33.3352, -60.2150, score=90),  # cerca de la salida
        _lead(3, -33.3349, -60.2100, score=70),  # en el medio
        _lead(4, -33.3000, -60.2100, score=99),  # 4 km al norte: fuera del desvío
        _lead(5, -33.3351, -60.2120, score=95, do_not_contact=True),
        _lead(6, -33.3351, -60.2110, score=95, status=LeadStatus.WON),
        _lead(7, None, None, score=99),
        _lead(8, -33.3350, -60.2080, score=10, raw_data={"ai_offer": {"probability": 80}}),
    ]
    plan = plan_route(leads, START, END, max_stops=3, max_detour_km=1.0)
    assert plan.candidates == 4  # 1, 2, 3 y 8
    assert [s.lead.id for s in plan.stops] == [2, 3, 8]  # por chance, en orden de oeste a este
    assert [s.chance for s in plan.stops] == [90, 70, 80]


def test_min_chance_and_round_trip():
    leads = [_lead(1, -33.3355, -60.2190, score=30), _lead(2, -33.3345, -60.2210, score=80)]
    plan = plan_route(leads, START, START, max_detour_km=1.0, min_chance=50)
    assert [s.lead.id for s in plan.stops] == [2]


def test_google_maps_links_are_split_in_legs():
    leads = [_lead(i, -33.3350, -60.2195 + i * 0.0009, score=50) for i in range(1, 21)]
    plan = plan_route(leads, START, END, max_stops=20, max_detour_km=1.0, mode="driving")
    links = plan.maps_links
    assert len(plan.stops) == 20 and len(links) == 3
    assert all("travelmode=driving" in link for link in links)
    assert "waypoints=" in links[0]
