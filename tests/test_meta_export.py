from datetime import datetime

import pytest
from sqlalchemy import select

from cmr_leads.db.models import InstagramPost
from cmr_leads.instagram.meta_export import MetaExportError, import_meta_export

STORIES = (
    '"identificador de la publicación","Identificador de la cuenta","Nombre de usuario de la cuenta",'
    '"Nombre de la cuenta",Descripción,"Duración (segundos)","Hora de publicación","Enlace permanente",'
    '"Tipo de publicación","Comentario sobre los datos",Fecha,Visualizaciones,Alcance,"Me gusta",'
    '"Veces que se ha compartido",Respuestas,Navegación,"Visitas al perfil","Toques en stickers",'
    '"Clics en el enlace"\n'
    '111,9,cuenta,"CUENTA",,38,"11/13/2026 01:18",https://www.instagram.com/stories/x/1,'
    '"Historia de Instagram",,Total,40,30,0,0,0,36,,,\n'
    '222,9,cuenta,"CUENTA","Hola",0,"08/20/2026 09:09",https://www.instagram.com/stories/x/2,'
    '"Historia de Instagram",,Total,950,669,0,3,1,53,2,,\n'
    '333,9,cuenta,"CUENTA",,0,"08/14/2026 16:44",https://www.instagram.com/stories/x/3,'
    '"Historia de Instagram",,Total,30,28,0,0,0,29\n'
)


def test_imports_stories_with_pacific_time(session, workspace):
    session.add(
        InstagramPost(
            workspace_id=workspace.id, media_id="111", product_type="STORY", views=10, reach=8
        )
    )
    session.flush()
    result = import_meta_export(session, workspace.id, STORIES.encode("utf-8"))
    assert (result.new_stories, result.updated_stories) == (2, 1)

    posts = {p.media_id: p for p in session.scalars(select(InstagramPost))}
    assert posts["111"].views == 40 and posts["111"].reach == 30  # números finales
    new = posts["222"]
    assert new.product_type == "STORY" and new.media_type == "IMAGE"
    assert new.published_at == datetime(2026, 8, 20, 16, 9)  # 09:09 PDT = 16:09 UTC
    assert (new.views, new.reach, new.shares, new.replies, new.profile_visits) == (
        950,
        669,
        3,
        1,
        2,
    )
    assert posts["333"].views == 30  # fila corta, sin columnas finales

    again = import_meta_export(session, workspace.id, STORIES.encode("utf-8"))
    assert again.new_stories == 0 and again.updated_stories == 3


def test_winter_time_and_feed_posts_are_not_overwritten(session, workspace):
    session.add(
        InstagramPost(workspace_id=workspace.id, media_id="444", product_type="FEED", views=5)
    )
    session.flush()
    csv_text = (
        '"identificador de la publicación",Descripción,"Duración (segundos)","Hora de publicación",'
        '"Enlace permanente","Tipo de publicación",Fecha,Visualizaciones,Alcance,"Me gusta",'
        '"Veces que se ha compartido",Seguidores,Comentarios,"Veces guardado"\n'
        '444,"Post",0,"11/04/2026 10:00",https://x/p/1,"Imagen de Instagram",Total,595,340,4,3,0,0,0\n'
        '555,"Reel",30,"11/12/2026 13:43",https://x/r/2,"Reel de Instagram",Total,771,515,10,4,1,1,2\n'
    )
    result = import_meta_export(session, workspace.id, csv_text.encode("utf-8"))
    assert result.new_posts == 1
    posts = {p.media_id: p for p in session.scalars(select(InstagramPost))}
    assert posts["444"].views == 5  # el feed lo trae la API
    assert (posts["444"].reach, posts["444"].follows) == (340, 0)  # completa lo que falta
    assert result.completed_posts == 1
    reel = posts["555"]
    assert reel.product_type == "REELS" and reel.media_type == "VIDEO"
    assert reel.published_at == datetime(2026, 11, 12, 21, 43)  # 13:43 PST = 21:43 UTC
    assert (reel.saves, reel.follows, reel.total_interactions) == (2, 1, 17)


def test_rejects_other_files(session, workspace):
    with pytest.raises(MetaExportError):
        import_meta_export(session, workspace.id, b"nombre,telefono\nPepe,123\n")


def _daily(title: str, rows: list[tuple[str, int]]) -> bytes:
    lines = ["sep=,", f'"{title}"', '"Fecha","Primary"']
    lines += [f'"{d}T01:00:00","{v}"' for d, v in rows]
    return ("\n".join(lines) + "\n").encode("utf-16")


def test_daily_export_fills_missing_days_and_fixes_clicks(session, workspace):
    from datetime import date

    from cmr_leads.db.models import InstagramDaily
    from cmr_leads.instagram.meta_export import import_daily_export, is_daily_export

    session.add(
        InstagramDaily(
            workspace_id=workspace.id, day=datetime(2026, 9, 23), views=100, website_clicks=7
        )
    )
    session.flush()
    today = date(2026, 11, 13)
    clicks = _daily("Clics en el enlace de Instagram", [("2026-09-23", 34), ("2026-11-13", 11)])
    assert is_daily_export(clicks)
    import_daily_export(session, workspace.id, clicks, today)
    views = _daily("Visualizaciones", [("2026-09-23", 999), ("2026-09-24", 50), ("2026-09-25", 0)])
    import_daily_export(session, workspace.id, views, today)
    rows = {r.day.date(): r for r in session.scalars(select(InstagramDaily))}
    assert rows[date(2026, 9, 23)].link_taps == 34  # aparte de los 7 del botón del sitio web
    assert rows[date(2026, 9, 23)].views == 100  # no pisa lo guardado
    assert rows[date(2026, 9, 24)].views == 50  # completa el día que faltaba
    assert date(2026, 9, 25) not in rows and date(2026, 11, 13) not in rows
    followers = _daily("Seguidores de Instagram", [("2026-09-23", 4), ("2026-09-26", 0)])
    import_daily_export(session, workspace.id, followers, today)
    assert rows[date(2026, 9, 23)].follows == 4
