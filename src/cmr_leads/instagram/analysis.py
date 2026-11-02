"""Qué funciona en el Instagram de CMR: tipo de publicación, día, horario y mejores posts."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from statistics import mean

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cmr_leads.db.models import InstagramAccountSnapshot, InstagramPost, Lead, LeadSource
from cmr_leads.domain.enums import SourceType

ARGENTINA = timedelta(hours=-3)  # sin horario de verano
WEEKDAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MIN_POSTS_PER_GROUP = 2
PLURAL = {"Reel / video": "reels", "Carrusel": "carruseles", "Imagen": "imágenes"}


def time_slot(hour: int) -> str:
    if 6 <= hour < 12:
        return "Mañana (6-12)"
    if 12 <= hour < 17:
        return "Siesta / tarde (12-17)"
    if 17 <= hour < 22:
        return "Tarde-noche (17-22)"
    return "Noche (22-6)"


@dataclass
class Group:
    name: str
    posts: int = 0
    avg_reach: float = 0
    avg_engagement: float | None = None


@dataclass
class InstagramReport:
    latest: InstagramAccountSnapshot | None = None
    previous: InstagramAccountSnapshot | None = None
    snapshots: list[InstagramAccountSnapshot] = field(default_factory=list)
    posts: list[InstagramPost] = field(default_factory=list)
    top_posts: list[InstagramPost] = field(default_factory=list)
    by_kind: list[Group] = field(default_factory=list)
    by_weekday: list[Group] = field(default_factory=list)
    by_slot: list[Group] = field(default_factory=list)
    dm_leads: int = 0
    recommendations: list[str] = field(default_factory=list)

    @property
    def follower_change(self) -> int | None:
        if (
            self.latest
            and self.previous
            and self.latest.followers is not None
            and self.previous.followers is not None
        ):
            return self.latest.followers - self.previous.followers
        return None


def _group(posts: list[InstagramPost], key) -> list[Group]:
    buckets: dict[str, list[InstagramPost]] = defaultdict(list)
    for post in posts:
        buckets[key(post)].append(post)
    groups = []
    for name, items in buckets.items():
        reaches = [p.reach for p in items if p.reach is not None]
        rates = [p.engagement_rate for p in items if p.engagement_rate is not None]
        groups.append(
            Group(name, len(items), mean(reaches) if reaches else 0, mean(rates) if rates else None)
        )
    return sorted(groups, key=lambda g: -g.avg_reach)


def _best(groups: list[Group]) -> Group | None:
    eligible = [g for g in groups if g.posts >= MIN_POSTS_PER_GROUP and g.avg_reach]
    return eligible[0] if len(eligible) >= 2 else None


def build_report(session: Session, workspace_id: int) -> InstagramReport:
    report = InstagramReport()
    report.snapshots = list(
        session.scalars(
            select(InstagramAccountSnapshot)
            .where(InstagramAccountSnapshot.workspace_id == workspace_id)
            .order_by(InstagramAccountSnapshot.day)
        )
    )
    if report.snapshots:
        report.latest = report.snapshots[-1]
        report.previous = report.snapshots[-2] if len(report.snapshots) > 1 else None
    report.posts = list(
        session.scalars(
            select(InstagramPost)
            .where(InstagramPost.workspace_id == workspace_id)
            .order_by(InstagramPost.published_at.desc())
        )
    )
    report.dm_leads = (
        session.scalar(
            select(func.count(func.distinct(Lead.id)))
            .join(LeadSource, LeadSource.lead_id == Lead.id)
            .where(Lead.workspace_id == workspace_id, LeadSource.source == SourceType.INSTAGRAM_DM)
        )
        or 0
    )

    dated = [p for p in report.posts if p.published_at]
    report.top_posts = sorted(
        report.posts, key=lambda p: (p.engagement_rate or 0, p.reach or 0), reverse=True
    )[:5]
    report.by_kind = _group(report.posts, lambda p: p.kind)
    report.by_weekday = _group(dated, lambda p: WEEKDAYS[(p.published_at + ARGENTINA).weekday()])
    report.by_slot = _group(dated, lambda p: time_slot((p.published_at + ARGENTINA).hour))
    report.recommendations = recommendations(report)
    return report


def recommendations(report: InstagramReport) -> list[str]:
    tips: list[str] = []
    if len(report.posts) < 6:
        return [
            "Con pocas publicaciones sincronizadas todavía no se pueden sacar conclusiones. Volvé a actualizar en unas semanas."
        ]
    kinds = [g for g in report.by_kind if g.posts >= MIN_POSTS_PER_GROUP and g.avg_reach]
    if len(kinds) >= 2 and kinds[-1].avg_reach:
        ratio = kinds[0].avg_reach / kinds[-1].avg_reach
        if ratio >= 1.3:
            best = PLURAL.get(kinds[0].name, kinds[0].name)
            worst = PLURAL.get(kinds[-1].name, kinds[-1].name)
            tips.append(
                f"Tus {best} llegan a {ratio:.1f} veces más gente que tus {worst}. Priorizá ese formato."
            )
    if best_day := _best(report.by_weekday):
        tips.append(
            f"Lo que publicás los {best_day.name.lower()} tiene más alcance (promedio {best_day.avg_reach:.0f})."
        )
    if best_slot := _best(report.by_slot):
        tips.append(f"El mejor horario viene siendo {best_slot.name.lower()}.")
    if report.top_posts and report.top_posts[0].engagement_rate:
        top = report.top_posts[0]
        snippet = (top.caption or "").split("\n")[0][:60]
        tips.append(
            f'Tu publicación con más interacción fue "{snippet}": repetí ese tema o formato.'
        )
    saves = [p for p in report.posts if p.saves]
    if saves and report.latest:
        best_save = max(saves, key=lambda p: p.saves or 0)
        if (best_save.saves or 0) >= 5:
            tips.append(
                "El contenido que la gente guarda (tips, antes/después, precios) es el que más sirve para atraer clientes: hacé más de eso."
            )
    latest = report.latest
    if latest and latest.profile_views and latest.website_clicks is not None:
        rate = latest.website_clicks / latest.profile_views if latest.profile_views else 0
        if rate < 0.05:
            tips.append(
                "Pocas visitas al perfil terminan en clic al link: revisá la bio y el botón de contacto (que lleve directo a WhatsApp)."
            )
    if report.dm_leads:
        tips.append(
            f'{report.dm_leads} negocio(s) te escribieron por Instagram: están en Leads con fuente "Instagram (te escribieron)".'
        )
    return tips or ["Todavía no hay diferencias claras. Seguí publicando y actualizá cada semana."]
