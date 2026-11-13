from datetime import date, datetime
from types import SimpleNamespace

from cmr_leads.ads.objectives import OBJECTIVES
from cmr_leads.db.models import InstagramPost
from cmr_leads.instagram.promote import ideas_facts, match_post, promotion_ideas

TODAY = date(2026, 11, 13)


def post(ws, n, caption, reach, likes=5, shares=0, saves=0, media="IMAGE", product="FEED"):
    return InstagramPost(
        workspace_id=ws.id,
        media_id=str(n),
        media_type=media,
        product_type=product,
        caption=caption,
        published_at=datetime(2026, 10, n),
        reach=reach,
        likes=likes,
        comments=0,
        shares=shares,
        saves=saves,
    )


def ad_row(name, spend, reach, result, objective="visitas_perfil"):
    return SimpleNamespace(
        name=name,
        totals=SimpleNamespace(spend=spend, reach=reach),
        result=result,
        goal=OBJECTIVES[objective],
    )


def test_match_post_by_caption_start(session, workspace):
    a = post(workspace, 1, "¿Te pasa alguna de estas?\n\n❌ Sin registro", 100)
    b = post(workspace, 2, "Otra cosa", 100)
    assert match_post("Instagram post: ¿Te pasa alguna de estas? \n\n❌ Sin...", [a, b]) is a
    assert match_post("Campaña de alcance", [a, b]) is None
    assert match_post("Instagram post: Otr...", [a, b]) is None  # muy corto para asegurar


def test_candidates_beat_typical_post_and_skip_promoted(session, workspace):
    session.add_all(
        [
            post(workspace, 1, "Típica uno", 300),
            post(workspace, 2, "Típica dos", 320),
            post(workspace, 3, "Típica tres", 280),
            post(workspace, 4, "La que explotó sola", 1500, shares=12, saves=3),
            post(workspace, 5, "Promocionada con plata grande", 5000),
            post(workspace, 6, "Historia", 9000, product="STORY"),
        ]
    )
    session.flush()
    rows = [ad_row("Instagram post: Promocionada con plata...", 10000, 2500, 120)]
    ideas = promotion_ideas(session, workspace.id, rows, TODAY)

    titles = [c["title"] for c in ideas["candidates"]]
    assert titles[0] == "La que explotó sola"
    assert "Promocionada con plata grande" not in titles  # su alcance incluye lo pagado
    assert "Historia" not in titles
    reasons = " ".join(ideas["candidates"][0]["reasons"])
    assert "compartieron 12" in reasons and "1.500 personas" in reasons
    assert [x["title"] for x in ideas["promoted"]] == ["Promocionada con plata grande"]


def test_promoted_verdict_by_cost_per_thousand(session, workspace):
    session.add_all(
        [
            post(workspace, 1, "Barata para llegar a la gente", 100),
            post(workspace, 2, "Cara para llegar a la gente", 100),
        ]
    )
    session.flush()
    rows = [
        ad_row("Instagram post: Barata para llegar...", 4000, 1000, 50),
        ad_row("Instagram post: Cara para llegar...", 8000, 500, 2, "mensajes"),
    ]
    ideas = promotion_ideas(session, workspace.id, rows, TODAY)
    verdicts = {x["title"]: x["verdict"] for x in ideas["promoted"]}
    assert verdicts == {
        "Barata para llegar a la gente": "Volver a promocionar",
        "Cara para llegar a la gente": "No repetir así",
    }
    facts = ideas_facts(ideas)
    assert facts["ya_promocionadas"][0]["costo_cada_1000"] == 4000
