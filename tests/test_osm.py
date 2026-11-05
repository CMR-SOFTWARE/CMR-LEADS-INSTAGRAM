from urllib.parse import unquote_plus

import httpx
import pytest

from cmr_leads.domain.enums import SourceType
from cmr_leads.sources.osm import OsmError, OsmSource, build_query, resolve_area

ELEMENTS = {
    "elements": [
        {
            "type": "node",
            "id": 1,
            "lat": -33.33,
            "lon": -60.22,
            "tags": {
                "name": "Peluquería Sofi",
                "shop": "hairdresser",
                "phone": "+54 9 336 412-3456",
                "addr:street": "Avenida Savio",
                "addr:housenumber": "123",
                "contact:instagram": "https://instagram.com/peluqueria.sofi",
            },
        },
        {"type": "way", "id": 2, "center": {"lat": -33.34, "lon": -60.21}, "tags": {"shop": "x"}},
    ]
}


def test_area_and_query():
    assert resolve_area("San Nicolás de los Arroyos, Buenos Aires")[:2] == (-33.3335, -60.2252)
    assert resolve_area("Ciudad inventada") is None
    assert 'shop="hairdresser"' in build_query("Peluquería", -33.3, -60.2, 5000)
    assert build_query("rubro raro", -33.3, -60.2, 5000) is None


def test_discover_maps_tags_and_skips_unnamed():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(502, text="busy")  # el primero saturado -> prueba el espejo
        return httpx.Response(200, json=ELEMENTS)

    source = OsmSource(
        "peluquería", "San Nicolás", httpx.Client(transport=httpx.MockTransport(handler))
    )
    records = list(source.discover())
    assert len(calls) == 2 and len(records) == 1
    record = records[0]
    assert record.source == SourceType.OSM
    assert record.profile.display_name == "Peluquería Sofi"
    assert record.profile.instagram_username == "peluqueria.sofi"
    assert record.profile.address == "Avenida Savio 123"
    assert record.profile.latitude == -33.33


def test_discover_reports_when_all_servers_busy():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(504)))
    with pytest.raises(OsmError, match="saturado"):
        list(OsmSource("gimnasio", "Ramallo", client).discover())


def test_same_business_from_google_and_osm_is_merged(session, workspace):
    from cmr_leads.domain.schemas import RawProfile, SourceRecord
    from cmr_leads.pipeline.ingest import ingest

    google = RawProfile(
        display_name="Peluquería Sofi",
        address="Av. Savio 123, San Nicolás",
        latitude=-33.3301,
        longitude=-60.2201,
    )
    osm = RawProfile(
        display_name="Peluqueria Sofi",
        address="Avenida Savio 123",
        latitude=-33.3302,
        longitude=-60.2202,
    )
    first = ingest(
        session, workspace, SourceRecord(profile=google, source=SourceType.GOOGLE_PLACES), "AR"
    )
    second = ingest(session, workspace, SourceRecord(profile=osm, source=SourceType.OSM), "AR")
    assert first.created and not second.created
    assert second.lead.id == first.lead.id


def test_osm_search_from_panel(monkeypatch, tmp_path):
    import shutil

    from fastapi.testclient import TestClient

    import cmr_leads.sources.osm as osm
    from cmr_leads.db.session import run_migrations
    from cmr_leads.settings import Settings
    from cmr_leads.web.app import create_app

    from .conftest import ROOT

    (tmp_path / "config").mkdir()
    shutil.copy(ROOT / "config" / "scoring.yaml", tmp_path / "config" / "scoring.yaml")
    url = f"sqlite:///{tmp_path / 'w.db'}"
    run_migrations(url)
    settings = Settings(
        _env_file=None,
        database_url=url,
        scoring_config=tmp_path / "config" / "scoring.yaml",
        backup_dir=tmp_path / "b",
        website_fetch_enabled=False,
    )
    real_init = osm.OsmSource.__init__

    def fake_init(self, rubro, area, http=None):
        client = httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=ELEMENTS))
        )
        real_init(self, rubro, area, client)

    monkeypatch.setattr(osm.OsmSource, "__init__", fake_init)
    with TestClient(create_app(settings)) as client:
        response = client.post("/importar/osm", data={"query": "peluquería", "area": "San Nicolás"})
        assert "OpenStreetMap: 1 nuevos" in unquote_plus(str(response.url))
        assert "Peluquería Sofi" in client.get("/leads?source=osm").text
