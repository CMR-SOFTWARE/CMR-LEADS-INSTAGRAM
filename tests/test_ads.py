import json
from datetime import date, datetime, timedelta
from urllib.parse import unquote_plus

import httpx
import pytest
from sqlalchemy import func, select

from cmr_leads.ads.alerts import evaluate_alerts, parse_amount
from cmr_leads.ads.client import AdsAuthError, AdsClient, AdsRateLimitedError
from cmr_leads.ads.metrics import account_totals, by_month, level_rows
from cmr_leads.ads.recommendations import build_recommendations
from cmr_leads.ads.report import build_report_data, rank_ads
from cmr_leads.ads.service import ensure_checklist, generate_report, run_ads_sync
from cmr_leads.ads.sync import ensure_account, history_start, standard_periods, sync_ads
from cmr_leads.db.models import (
    Ad,
    AdAccount,
    AdAlert,
    AdCampaign,
    AdDailyMetric,
    AdPeriodMetric,
    AdReport,
)
from cmr_leads.settings import Settings

ACT = "act_123"
TODAY = date(2026, 11, 13)


class FakeMeta:
    """Simula la Marketing API según la ruta y los parámetros pedidos."""

    def __init__(self, ad_status="ACTIVE", account_status=1, spend_cap="0", amount_spent="0"):
        self.ad_status = ad_status
        self.account_status = account_status
        self.spend_cap = spend_cap
        self.amount_spent = amount_spent
        self.calls: list[str] = []
        self.daily_rows: list[dict] = []
        self.fail_with: dict | None = None

    def add_day(self, ad_id, when: date, spend, messages, impressions=1000, clicks=20):
        self.daily_rows.append(
            {
                "ad_id": ad_id,
                "ad_name": f"Anuncio {ad_id}",
                "adset_id": "s1",
                "adset_name": "Conjunto 1",
                "campaign_id": "c1",
                "campaign_name": "CMR Nexo",
                "date_start": when.isoformat(),
                "date_stop": when.isoformat(),
                "spend": str(spend),
                "impressions": str(impressions),
                "reach": str(impressions // 2),
                "clicks": str(clicks),
                "inline_link_clicks": str(clicks // 2),
                "actions": [
                    {
                        "action_type": "onsite_conversion.messaging_conversation_started_7d",
                        "value": str(messages),
                    },
                    {"action_type": "link_click", "value": "5"},
                ],
            }
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/v23.0/")[1]
        params = request.url.params
        self.calls.append(path)
        if self.fail_with:
            return httpx.Response(400, json={"error": self.fail_with})
        if path == ACT:
            if params["fields"] == "funding_source_details":
                return httpx.Response(
                    200,
                    json={"funding_source_details": {"display_string": "Visa *1234"}},
                )
            return httpx.Response(
                200,
                json={
                    "name": "CMR Software",
                    "currency": "ARS",
                    "timezone_name": "America/Argentina/Buenos_Aires",
                    "account_status": self.account_status,
                    "balance": "150000",
                    "spend_cap": self.spend_cap,
                    "amount_spent": self.amount_spent,
                    "is_prepay_account": False,
                },
            )
        if path == f"{ACT}/campaigns":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "c1",
                            "name": "CMR Nexo",
                            "objective": "OUTCOME_ENGAGEMENT",
                            "status": "ACTIVE",
                            "effective_status": "ACTIVE",
                            "daily_budget": "600000",
                        }
                    ]
                },
            )
        if path == f"{ACT}/adsets":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "s1",
                            "name": "Conjunto 1",
                            "campaign_id": "c1",
                            "effective_status": "ACTIVE",
                            "optimization_goal": "CONVERSATIONS",
                            "destination_type": "INSTAGRAM_DIRECT",
                        }
                    ]
                },
            )
        if path == f"{ACT}/ads":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "a1",
                            "name": "Reel peluquera",
                            "campaign_id": "c1",
                            "adset_id": "s1",
                            "effective_status": "ACTIVE",
                            "creative": {"thumbnail_url": "https://x/1.jpg"},
                        },
                        {
                            "id": "a2",
                            "name": "Carrusel rubros",
                            "campaign_id": "c1",
                            "adset_id": "s1",
                            "effective_status": self.ad_status,
                            "ad_review_feedback": {"global": {"texto": "Texto engañoso"}},
                        },
                    ]
                },
            )
        if path == f"{ACT}/insights":
            time_range = json.loads(params["time_range"])
            since = date.fromisoformat(time_range["since"])
            until = date.fromisoformat(time_range["until"])
            if params.get("time_increment") == "1":
                rows = [
                    r
                    for r in self.daily_rows
                    if since <= date.fromisoformat(r["date_start"]) <= until
                ]
                return httpx.Response(200, json={"data": rows})
            level = params["level"]
            id_field = {"account": "account_id", "campaign": "campaign_id", "ad": "ad_id"}[level]
            ids = {"account": ["123"], "campaign": ["c1"], "ad": ["a1", "a2"]}[level]
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            id_field: i,
                            "reach": "700",
                            "frequency": "1.8",
                            "impressions": "1300",
                            "spend": "100",
                        }
                        for i in ids
                    ]
                },
            )
        return httpx.Response(404, json={"error": {"code": 100, "message": path}})

    def client(self, **kwargs) -> AdsClient:
        return AdsClient(
            "token",
            "v23.0",
            http=httpx.Client(transport=httpx.MockTransport(self.handler)),
            sleep=lambda _s: None,
            **kwargs,
        )


def _settings(tmp_path, **extra) -> Settings:
    return Settings(
        _env_file=None,
        meta_ads_access_token="token",
        meta_ad_account_id="123",
        meta_ads_reports_dir=tmp_path / "reportes",
        **extra,
    )


# ---------------- Cliente ----------------


def test_client_retries_rate_limit_then_succeeds():
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(
                400, json={"error": {"code": 17, "message": "User request limit"}}
            )
        return httpx.Response(200, json={"ok": True})

    waits = []
    client = AdsClient(
        "t", http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=waits.append
    )
    assert client.get("act_1") == {"ok": True}
    assert len(attempts) == 3
    assert waits[1] > waits[0]  # espera cada vez más


def test_client_gives_up_after_retries_and_maps_auth_errors():
    limited = httpx.MockTransport(
        lambda r: httpx.Response(400, json={"error": {"code": 80004, "message": "too many"}})
    )
    client = AdsClient("t", http=httpx.Client(transport=limited), sleep=lambda _s: None)
    with pytest.raises(AdsRateLimitedError):
        client.get("act_1")
    assert client.requests_made == 5

    expired = httpx.MockTransport(
        lambda r: httpx.Response(400, json={"error": {"code": 190, "message": "expired"}})
    )
    client = AdsClient("t", http=httpx.Client(transport=expired), sleep=lambda _s: None)
    with pytest.raises(AdsAuthError):
        client.get("act_1")
    assert client.requests_made == 1  # sin reintentos


def test_client_follows_pagination():
    def handler(request):
        if "after" in request.url.params:
            return httpx.Response(200, json={"data": [{"id": "2"}]})
        return httpx.Response(
            200,
            json={
                "data": [{"id": "1"}],
                "paging": {
                    "next": "https://graph.facebook.com/v23.0/act_1/ads?after=x&access_token=t"
                },
            },
        )

    client = AdsClient("t", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert [r["id"] for r in client.get_all("act_1/ads")] == ["1", "2"]


# ---------------- Sincronización ----------------


def _seed_days(meta: FakeMeta):
    # Mes anterior y este mes, para poder comparar.
    meta.add_day("a1", date(2026, 10, 10), 3000, 3)
    meta.add_day("a1", date(2026, 11, 10), 2000, 4)
    meta.add_day("a2", date(2026, 11, 11), 1500, 0)
    meta.add_day("a9", date(2026, 11, 12), 500, 1)  # anuncio borrado: ya no está en el listado


def test_sync_creates_structure_metrics_and_periods(session, workspace):
    meta = FakeMeta()
    _seed_days(meta)
    result = sync_ads(session, workspace.id, meta.client(), ACT, TODAY)

    account = session.scalar(select(AdAccount))
    assert account.name == "CMR Software"
    assert account.balance == 1500.0  # viene en centavos
    assert account.funding_display == "Visa *1234"
    campaign = session.scalar(select(AdCampaign))
    assert campaign.daily_budget == 6000.0
    assert result.since == date(2026, 1, 1)  # primera vez: desde el 1 de enero
    assert ensure_account(session, workspace.id, ACT).history_since == datetime(2026, 1, 1)
    assert result.metric_rows == 4
    stub = session.scalar(select(Ad).where(Ad.meta_id == "a9"))
    assert stub.effective_status == "ARCHIVED"
    metric = session.scalar(
        select(AdDailyMetric).where(AdDailyMetric.day == datetime(2026, 11, 10))
    )
    assert metric.messages == 4 and metric.spend == 2000.0 and metric.link_clicks == 10
    # 5 períodos × (1 cuenta + 1 campaña + 2 anuncios)
    assert session.scalar(select(func.count(AdPeriodMetric.id))) == 20


def test_missing_history_is_backfilled_from_january(session, workspace):
    meta = FakeMeta()
    _seed_days(meta)
    account = ensure_account(session, workspace.id, ACT)
    account.history_since = datetime(2026, 10, 1)  # cuenta sincronizada antes de "Este año"
    result = sync_ads(session, workspace.id, meta.client(), ACT, TODAY)
    assert result.since == date(2026, 1, 1)
    assert account.history_since == datetime(2026, 1, 1)
    assert sync_ads(session, workspace.id, meta.client(), ACT, TODAY).since > date(2026, 1, 1)


def test_history_start_and_year_period():
    assert history_start(TODAY) == date(2026, 1, 1)
    assert history_start(date(2027, 1, 10)) == date(2026, 12, 1)  # en enero, también diciembre
    assert standard_periods(TODAY)["anio"] == (date(2026, 1, 1), TODAY)


def test_by_month_groups_series():
    series = [
        {"day": date(2026, 10, 30), "spend": 100.0, "messages": 1},
        {"day": date(2026, 11, 1), "spend": 50.0, "messages": 2},
        {"day": date(2026, 11, 2), "spend": 25.0, "messages": 0},
    ]
    months = by_month(series)
    assert [(m["day"], m["spend"], m["messages"]) for m in months] == [
        (date(2026, 10, 1), 100.0, 1),
        (date(2026, 11, 1), 75.0, 2),
    ]
    assert months[0]["reach"] == months[0]["impressions"] == months[0]["link_clicks"] == 0


def test_second_sync_updates_without_duplicates(session, workspace):
    meta = FakeMeta()
    _seed_days(meta)
    sync_ads(session, workspace.id, meta.client(), ACT, TODAY)
    meta.daily_rows[1]["spend"] = "2500"  # Meta corrigió el dato
    result = sync_ads(session, workspace.id, meta.client(), ACT, TODAY)

    assert result.since == TODAY - timedelta(days=6)  # ya hay datos: solo los últimos 7 días
    assert session.scalar(select(func.count(AdDailyMetric.id))) == 4
    metric = session.scalar(
        select(AdDailyMetric).where(AdDailyMetric.day == datetime(2026, 11, 10))
    )
    assert metric.spend == 2500.0


def test_totals_use_period_reach_not_sum_of_days(session, workspace):
    meta = FakeMeta()
    _seed_days(meta)
    sync_ads(session, workspace.id, meta.client(), ACT, TODAY)
    since, until = standard_periods(TODAY)["mes"]
    totals = account_totals(session, workspace.id, since, until, ACT)
    assert totals.spend == 4000.0
    assert totals.messages == 5
    assert totals.cost_per_message == 800.0
    assert totals.reach == 700  # el del período, no la suma diaria (500 + 500 + 500)

    rows = level_rows(session, workspace.id, "anuncios", since, until)
    assert [r.name for r in rows] == ["Reel peluquera", "Carrusel rubros", "Anuncio a9"]
    assert rows[1].totals.cost_per_message is None


# ---------------- Alertas ----------------


def test_alerts_open_and_close_by_themselves(session, workspace):
    meta = FakeMeta(ad_status="DISAPPROVED", spend_cap="2000000", amount_spent="1950000")
    sync_ads(session, workspace.id, meta.client(), ACT, TODAY)
    account = session.scalar(select(AdAccount))
    created = evaluate_alerts(session, workspace.id, account, TODAY, 30, 20000)
    kinds = sorted(a.kind for a in created)
    assert kinds == ["anuncio_rechazado", "sin_saldo"]
    rejected = next(a for a in created if a.kind == "anuncio_rechazado")
    assert "Texto engañoso" in rejected.message
    assert "—" not in rejected.message

    # Se vuelve a evaluar igual: no se duplican.
    assert evaluate_alerts(session, workspace.id, account, TODAY, 30, 20000) == []

    # Se corrige el anuncio y se sube el límite: se cierran solas.
    meta.ad_status, meta.spend_cap = "ACTIVE", "0"
    sync_ads(session, workspace.id, meta.client(), ACT, TODAY)
    evaluate_alerts(session, workspace.id, account, TODAY, 30, 20000)
    assert session.scalar(select(func.count(AdAlert.id)).where(AdAlert.status == "abierta")) == 0


def test_cost_rise_alert(session, workspace):
    meta = FakeMeta()
    for offset in range(7, 14):  # semana anterior: $1.000 por mensaje
        meta.add_day("a1", TODAY - timedelta(days=offset), 1000, 1)
    for offset in range(0, 7):  # esta semana: $2.000 por mensaje
        meta.add_day("a1", TODAY - timedelta(days=offset), 2000, 1)
    sync_ads(session, workspace.id, meta.client(), ACT, TODAY)
    account = session.scalar(select(AdAccount))
    created = evaluate_alerts(session, workspace.id, account, TODAY, 30, 0)
    assert [a.kind for a in created] == ["costo_en_alza"]
    assert "subió 100%" in created[0].title


def test_parse_prepaid_balance():
    assert parse_amount("Saldo disponible ($12.345,67 ARS)") == 12345.67
    assert parse_amount("Available balance ($1,234.50 USD)") == 1234.5
    assert parse_amount("Visa *1234") == 1234.0
    assert parse_amount(None) is None


# ---------------- Reporte ----------------


def test_rank_ads_best_and_worst_do_not_overlap():
    # Se ordena por costo de llegar a 1.000 personas (la prioridad es el alcance).
    ads = [
        {"name": "A", "spend": 1000, "reach": 10000, "cost_per_thousand_reach": 100, "result": 50},
        {"name": "B", "spend": 1000, "reach": 2000, "cost_per_thousand_reach": 500, "result": 9},
        {"name": "C", "spend": 800, "reach": 4000, "cost_per_thousand_reach": 200, "result": 0},
        {"name": "D", "spend": 0, "reach": None, "cost_per_thousand_reach": None, "result": 0},
    ]
    best, worst = rank_ads(ads)
    assert [a["name"] for a in best] == ["A"]  # 3 anuncios con gasto: 1 mejor
    # Primero el que no trajo el resultado de su objetivo, después el alcance más caro.
    assert [a["name"] for a in worst] == ["C", "B"]

    best, worst = rank_ads(ads[:2])
    assert [a["name"] for a in best] == ["A"] and [a["name"] for a in worst] == ["B"]


def test_recommendations_rules():
    def ad(name, spend, result, reach, objective="visitas_perfil"):
        return {
            "name": name,
            "spend": spend,
            "result": result,
            "reach": reach,
            "objective": objective,
            "result_label": "Visitas al perfil",
            "result_unit": "visita",
            "cost_per_result": spend / result if result else None,
            "cost_per_thousand_reach": spend / reach * 1000,
        }

    data = {
        "totals": {"spend": 10000, "frequency": 3.6, "cost_per_thousand_reach": 1800},
        "previous": {"cost_per_thousand_reach": 1000},
        "planned_budget": 30000,
        "ads": [
            ad("Bueno", 4000, 100, 3000),
            ad("Caro", 4000, 20, 2500),
            ad("Nada", 2000, 0, 200),
        ],
    }
    data["best_ads"] = [data["ads"][0]]
    recs = build_recommendations(data)
    kinds = [r["tipo"] for r in recs]
    assert kinds[:4] == ["pausar", "cambiar", "presupuesto", "frecuencia"]
    assert "se pagó para visitas al perfil" in recs[0]["texto"]
    assert len(recs) <= 6
    for rec in recs:
        assert "—" not in rec["texto"]
        assert "inteligencia artificial" not in rec["texto"].lower()

    assert build_recommendations({"totals": {"spend": 0}, "ads": []})[0]["tipo"] == "arrancar"


def test_generate_report_with_pdf(session, workspace, tmp_path):
    meta = FakeMeta()
    _seed_days(meta)
    sync_ads(session, workspace.id, meta.client(), ACT, TODAY)
    settings = _settings(tmp_path)
    report = generate_report(session, workspace.id, settings, date(2026, 11, 1), meta.client())

    data = report.data
    assert data["label"] == "noviembre 2026"
    assert data["totals"]["spend"] == 4000.0
    assert data["previous"]["spend"] == 3000.0
    assert data["changes"]["spend"] == pytest.approx(1 / 3)
    assert data["best_ads"][0]["name"] == "Reel peluquera"
    assert data["worst_ads"][0]["name"] == "Carrusel rubros"
    assert data["recommendations"]
    pdf = tmp_path / "reportes" / "reporte-publicidad-2026-11.pdf"
    assert pdf.read_bytes().startswith(b"%PDF")
    assert build_report_data(session, workspace.id, ACT, date(2026, 11, 1))["month"] == "2026-11"


def test_run_ads_sync_generates_last_month_report_and_records_errors(session, workspace, tmp_path):
    meta = FakeMeta()
    _seed_days(meta)
    settings = _settings(tmp_path)
    message = run_ads_sync(session, workspace.id, settings, TODAY, meta.client())
    assert "Reporte de 10/2026 generado" in message
    assert session.scalar(select(AdReport.month)) == "2026-10"

    meta.fail_with = {"code": 190, "message": "Session has expired"}
    message = run_ads_sync(session, workspace.id, settings, TODAY, meta.client())
    assert "No se pudo actualizar" in message
    alert = session.scalar(select(AdAlert).where(AdAlert.kind == "error_sincronizacion"))
    assert alert.status == "abierta"

    meta.fail_with = None
    run_ads_sync(session, workspace.id, settings, TODAY, meta.client())
    session.refresh(alert)
    assert alert.status == "resuelta"


def test_run_ads_sync_without_config(session, workspace):
    message = run_ads_sync(session, workspace.id, Settings(_env_file=None))
    assert "Falta configurar" in message


def test_run_ads_sync_skips_when_another_sync_is_running(session, workspace, tmp_path):
    from cmr_leads.ads import service

    meta = FakeMeta()
    with service._SYNC_LOCK:
        message = run_ads_sync(session, workspace.id, _settings(tmp_path), TODAY, meta.client())
    assert message == service.SYNC_BUSY
    assert meta.calls == []


def test_checklist_is_seeded_once(session, workspace):
    items = ensure_checklist(session, workspace.id)
    assert [i.key for i in items][:2] == ["business_manager", "pagina_instagram"]
    assert len(ensure_checklist(session, workspace.id)) == 6


# ---------------- Panel web ----------------


@pytest.fixture
def web(tmp_path):
    import shutil

    from fastapi.testclient import TestClient

    from cmr_leads.db.session import run_migrations
    from cmr_leads.web.app import create_app

    from .conftest import ROOT

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy(ROOT / "config" / "scoring.yaml", config_dir / "scoring.yaml")
    url = f"sqlite:///{tmp_path / 'web.db'}"
    run_migrations(url)
    settings = Settings(
        _env_file=None,
        database_url=url,
        scoring_config=config_dir / "scoring.yaml",
        website_fetch_enabled=False,
        backup_dir=tmp_path / "backups",
        meta_ads_reports_dir=tmp_path / "reportes",
        meta_ad_account_id="123",
        meta_ads_sync_hours=0,
    )
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        client.app_state = app.state
        yield client


def _seed_web(web):
    from cmr_leads.db.session import get_or_create_workspace

    meta = FakeMeta(ad_status="DISAPPROVED")
    today = date.today()  # relativo a hoy: estos tests usan la fecha real del panel
    meta.add_day("a1", today, 2000, 4)
    meta.add_day("a2", today, 1500, 0)
    with web.app_state.session_factory() as session:
        ws = get_or_create_workspace(session, "cmr")
        sync_ads(session, ws.id, meta.client(), ACT, date.today())
        account = session.scalar(select(AdAccount))
        evaluate_alerts(session, ws.id, account, date.today(), 30, 0)
        session.commit()


def test_ads_pages_render_empty(web):
    for url in (
        "/publicidad",
        "/publicidad?periodo=rango&desde=2026-10-01&hasta=2026-10-31&nivel=anuncios",
        "/publicidad/alertas",
        "/publicidad/alertas?ver=todas",
        "/publicidad/reportes",
        "/publicidad/configuracion",
    ):
        response = web.get(url)
        assert response.status_code == 200, url
        assert "Publicidad" in response.text
        assert "—" not in response.text.split('id="contenido"')[1], url


def test_ads_pages_with_data(web):
    _seed_web(web)
    page = web.get("/publicidad?periodo=30d").text
    assert "Reel peluquera" not in page  # el nivel por defecto son campañas
    assert "CMR Nexo" in page
    page = web.get("/publicidad?periodo=30d&nivel=anuncios").text
    # El formulario con "Todas" manda campana vacía: no tiene que romper.
    filtered = web.get("/publicidad?nivel=campanas&periodo=anio&desde=&hasta=&campana=")
    assert filtered.status_code == 200
    assert "Del 01/01/" in filtered.text
    assert "Reel peluquera" in page and "Rechazado" in page
    alerts = web.get("/publicidad/alertas").text
    assert "Anuncio rechazado: Carrusel rubros" in alerts


def test_ads_checklist_and_alert_actions(web):
    _seed_web(web)
    response = web.post(
        "/publicidad/configuracion/dominio", data={"status": "listo", "notes": "3 dominios OK"}
    )
    assert response.status_code == 200
    assert "Dominio verificado: Listo" in unquote_plus(str(response.url))
    assert 'value="3 dominios OK"' in web.get("/publicidad/configuracion").text
    assert web.post("/publicidad/configuracion/dominio", data={"status": "x"}).status_code == 200

    with web.app_state.session_factory() as session:
        alert_id = session.scalar(select(AdAlert.id))
    web.post(f"/publicidad/alertas/{alert_id}/resolver")
    with web.app_state.session_factory() as session:
        assert session.get(AdAlert, alert_id).status == "resuelta"


def test_ads_report_generation_and_pdf_download(web):
    _seed_web(web)
    month = f"{date.today():%Y-%m}"
    response = web.post("/publicidad/reportes/generar", data={"mes": month})
    assert response.status_code == 200
    assert "Recomendaciones para el mes que viene" in response.text
    pdf = web.get(f"/publicidad/reportes/{month}/pdf")
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    for old in web.app.state.settings.meta_ads_reports_dir.glob("*.pdf"):
        old.unlink()  # como en la nube, donde /tmp se borra
    again = web.get(f"/publicidad/reportes/{month}/pdf")
    assert again.content.startswith(b"%PDF")
    assert month in web.get("/publicidad/reportes").text
    missing = web.get("/publicidad/reportes/2001-01")
    assert "todavía no existe" in unquote_plus(str(missing.url))


def test_ads_sync_button_without_token(web):
    response = web.post("/publicidad/actualizar", data={"next": "//evil.com"})
    assert response.url.path == "/publicidad"
    assert "Falta configurar" in unquote_plus(str(response.url))


def test_each_campaign_is_measured_by_what_it_paid_for():
    from cmr_leads.ads.metrics import Totals
    from cmr_leads.ads.objectives import cost_per_result, objective_key

    # Casos reales de la cuenta de CMR.
    assert objective_key("LINK_CLICKS", "VISIT_INSTAGRAM_PROFILE", "INSTAGRAM_PROFILE") == (
        "visitas_perfil"
    )
    assert objective_key("OUTCOME_ENGAGEMENT", "LEAD_GENERATION", "INSTAGRAM_DIRECT") == "mensajes"
    assert objective_key("OUTCOME_AWARENESS", "REACH", None) == "alcance"
    assert objective_key("OUTCOME_ENGAGEMENT", "POST_ENGAGEMENT", None) == "interaccion"
    assert objective_key(None, None, None) == "alcance"

    totals = Totals(spend=15147, link_clicks=151, messages=0, reach=3393, engagement=1083)
    assert round(cost_per_result("visitas_perfil", totals)) == 100  # $100 por visita
    assert round(cost_per_result("alcance", totals)) == 4464  # cada 1.000 personas
    assert cost_per_result("mensajes", totals) is None
