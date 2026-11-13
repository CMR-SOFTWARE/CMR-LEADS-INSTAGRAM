from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from cmr_leads.domain.enums import EnrichmentState, LeadStatus


def utcnow() -> datetime:
    """UTC naive: SQLite no guarda zona horaria y así las comparaciones son consistentes."""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)

    instagram_user_id: Mapped[str | None] = mapped_column(String(64))
    instagram_username: Mapped[str | None] = mapped_column(String(64), index=True)
    google_place_id: Mapped[str | None] = mapped_column(String(200))
    display_name: Mapped[str | None] = mapped_column(String(200))
    bio: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(120))
    business_type: Mapped[str | None] = mapped_column(String(60))

    city: Mapped[str | None] = mapped_column(String(120))
    province: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(2))
    address: Mapped[str | None] = mapped_column(String(300))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    phone_e164: Mapped[str | None] = mapped_column(String(20))
    whatsapp_e164: Mapped[str | None] = mapped_column(String(20))
    whatsapp_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    email: Mapped[str | None] = mapped_column(String(200))
    website: Mapped[str | None] = mapped_column(String(500))
    website_domain: Mapped[str | None] = mapped_column(String(200))
    has_link_in_bio_only: Mapped[bool] = mapped_column(Boolean, default=False)

    followers: Mapped[int | None] = mapped_column(Integer)
    following: Mapped[int | None] = mapped_column(Integer)
    posts_count: Mapped[int | None] = mapped_column(Integer)
    is_business_account: Mapped[bool | None] = mapped_column(Boolean)

    is_business: Mapped[bool | None] = mapped_column(Boolean)
    is_local: Mapped[bool | None] = mapped_column(Boolean)
    fit_score: Mapped[int | None] = mapped_column(Integer)
    need_score: Mapped[int | None] = mapped_column(Integer)
    reach_score: Mapped[int | None] = mapped_column(Integer)
    business_score: Mapped[int | None] = mapped_column(Integer, index=True)
    priority: Mapped[str | None] = mapped_column(String(20))
    score_breakdown: Mapped[list[Any] | None] = mapped_column(JSON)
    scoring_version: Mapped[str | None] = mapped_column(String(20))
    recommended_service: Mapped[str | None] = mapped_column(String(40))
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    exclusion_reason: Mapped[str | None] = mapped_column(String(200))
    ai_score: Mapped[int | None] = mapped_column(Integer)
    ai_summary: Mapped[str | None] = mapped_column(Text)

    # Feedback humano para calibrar el scoring: 1 = buen lead, -1 = mal lead.
    feedback: Mapped[int | None] = mapped_column(Integer)
    feedback_reason: Mapped[str | None] = mapped_column(String(60))
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime)

    enrichment_state: Mapped[str] = mapped_column(String(20), default=EnrichmentState.PENDING)
    status: Mapped[str] = mapped_column(String(20), default=LeadStatus.NEW, index=True)
    do_not_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    assigned_to: Mapped[str | None] = mapped_column(String(120))
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    contacted_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_follow_up_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Embudo de ventas
    next_step: Mapped[str | None] = mapped_column(String(300))
    stage_entered_at: Mapped[datetime | None] = mapped_column(DateTime)
    lost_reason: Mapped[str | None] = mapped_column(String(40))
    deal_amount_ars: Mapped[int | None] = mapped_column(Integer)
    web_status: Mapped[str | None] = mapped_column(String(10))

    identifiers: Mapped[list["LeadIdentifier"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    sources: Mapped[list["LeadSource"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    interactions: Mapped[list["Interaction"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", order_by="Interaction.created_at"
    )
    status_changes: Mapped[list["StatusChange"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", order_by="StatusChange.changed_at"
    )
    meetings: Mapped[list["Meeting"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", order_by="Meeting.scheduled_at"
    )
    proposals: Mapped[list["Proposal"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", order_by="Proposal.created_at"
    )

    @property
    def instagram_url(self) -> str | None:
        if not self.instagram_username:
            return None
        return f"https://www.instagram.com/{self.instagram_username}/"

    @property
    def label(self) -> str:
        if self.display_name:
            return self.display_name
        if self.instagram_username:
            return f"@{self.instagram_username}"
        return f"Lead #{self.id}"


class LeadIdentifier(Base):
    """Identificadores normalizados para deduplicar (único por workspace)."""

    __tablename__ = "lead_identifiers"
    __table_args__ = (UniqueConstraint("workspace_id", "type", "value"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(20))
    value: Mapped[str] = mapped_column(String(200))

    lead: Mapped[Lead] = relationship(back_populates="identifiers")


class LeadSource(Base):
    """De dónde salió un lead. Un lead puede tener varias fuentes."""

    __tablename__ = "lead_sources"
    __table_args__ = (UniqueConstraint("lead_id", "source", "source_ref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    source_ref: Mapped[str] = mapped_column(String(200), default="")
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    lead: Mapped[Lead] = relationship(back_populates="sources")


class Interaction(Base):
    __tablename__ = "interactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(20))
    direction: Mapped[str] = mapped_column(String(10), default="outbound")
    message: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(String(60))
    # Plantilla usada (mensajes enviados) y clasificación de la respuesta (mensajes recibidos).
    template: Mapped[str | None] = mapped_column(String(40))
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    lead: Mapped[Lead] = relationship(back_populates="interactions")


class StatusChange(Base):
    __tablename__ = "status_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    lead: Mapped[Lead] = relationship(back_populates="status_changes")


class Meeting(Base):
    """Reunión o visita con un lead: cuándo, qué se habló y cómo sigue."""

    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    next_steps: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    lead: Mapped[Lead] = relationship(back_populates="meetings")


class Proposal(Base):
    """Propuesta en ARS armada desde config/precios.yaml."""

    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    plan: Mapped[str] = mapped_column(String(40))
    items: Mapped[list[Any]] = mapped_column(JSON)
    monthly_ars: Mapped[int] = mapped_column(Integer, default=0)
    one_time_ars: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="borrador")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)

    lead: Mapped[Lead] = relationship(back_populates="proposals")


class ApiUsage(Base):
    """Una fila por llamada a una API externa con cupo (para topes diarios)."""

    __tablename__ = "api_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    provider: Mapped[str] = mapped_column(String(40))
    used_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class InstagramAccountSnapshot(Base):
    """Estadísticas de la cuenta de Instagram de CMR, una fila por día sincronizado."""

    __tablename__ = "instagram_account_snapshots"
    __table_args__ = (UniqueConstraint("workspace_id", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    day: Mapped[datetime] = mapped_column(DateTime)
    followers: Mapped[int | None] = mapped_column(Integer)
    media_count: Mapped[int | None] = mapped_column(Integer)
    # Totales de los últimos `period_days` días según la API.
    period_days: Mapped[int] = mapped_column(Integer, default=30)
    reach: Mapped[int | None] = mapped_column(Integer)
    views: Mapped[int | None] = mapped_column(Integer)
    profile_views: Mapped[int | None] = mapped_column(Integer)
    website_clicks: Mapped[int | None] = mapped_column(Integer)
    accounts_engaged: Mapped[int | None] = mapped_column(Integer)
    total_interactions: Mapped[int | None] = mapped_column(Integer)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class InstagramPost(Base):
    __tablename__ = "instagram_posts"
    __table_args__ = (UniqueConstraint("workspace_id", "media_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    media_id: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str | None] = mapped_column(String(30))
    product_type: Mapped[str | None] = mapped_column(String(30))
    caption: Mapped[str | None] = mapped_column(Text)
    permalink: Mapped[str | None] = mapped_column(String(300))
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    reach: Mapped[int | None] = mapped_column(Integer)
    views: Mapped[int | None] = mapped_column(Integer)
    saves: Mapped[int | None] = mapped_column(Integer)
    shares: Mapped[int | None] = mapped_column(Integer)
    total_interactions: Mapped[int | None] = mapped_column(Integer)
    profile_visits: Mapped[int | None] = mapped_column(Integer)
    follows: Mapped[int | None] = mapped_column(Integer)
    replies: Mapped[int | None] = mapped_column(Integer)  # respuestas a historias
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def is_story(self) -> bool:
        return self.product_type == "STORY"

    @property
    def kind(self) -> str:
        if self.is_story:
            return "Historia"
        if self.product_type == "REELS" or self.media_type == "VIDEO":
            return "Reel / video"
        if self.media_type == "CAROUSEL_ALBUM":
            return "Carrusel"
        return "Imagen"

    @property
    def interactions(self) -> int:
        if self.total_interactions is not None:
            return self.total_interactions
        return sum(v or 0 for v in (self.likes, self.comments, self.saves, self.shares))

    @property
    def engagement_rate(self) -> float | None:
        return self.interactions / self.reach if self.reach else None


# ---------------- Publicidad (Meta Ads) ----------------
# Montos siempre en la moneda de la cuenta (ARS), ya convertidos desde centavos.


class AdAccount(Base):
    """Cuenta publicitaria de Meta conectada (act_...)."""

    __tablename__ = "ad_accounts"
    __table_args__ = (UniqueConstraint("workspace_id", "meta_account_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    meta_account_id: Mapped[str] = mapped_column(String(40))
    name: Mapped[str | None] = mapped_column(String(200))
    currency: Mapped[str | None] = mapped_column(String(10))
    timezone_name: Mapped[str | None] = mapped_column(String(60))
    account_status: Mapped[int | None] = mapped_column(Integer)
    disable_reason: Mapped[int | None] = mapped_column(Integer)
    is_prepay: Mapped[bool | None] = mapped_column(Boolean)
    balance: Mapped[float | None] = mapped_column(Float)
    spend_cap: Mapped[float | None] = mapped_column(Float)
    amount_spent: Mapped[float | None] = mapped_column(Float)
    funding_display: Mapped[str | None] = mapped_column(String(200))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_full_resync_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Desde qué día están guardadas las métricas diarias (para completar historia faltante).
    history_since: Mapped[datetime | None] = mapped_column(DateTime)
    last_sync_error: Mapped[str | None] = mapped_column(Text)

    @property
    def is_active(self) -> bool:
        return self.account_status in (None, 1)

    @property
    def spend_cap_left(self) -> float | None:
        if not self.spend_cap:
            return None
        return max(0.0, self.spend_cap - (self.amount_spent or 0))


class AdCampaign(Base):
    __tablename__ = "ad_campaigns"
    __table_args__ = (UniqueConstraint("workspace_id", "meta_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    account_id: Mapped[int] = mapped_column(
        ForeignKey("ad_accounts.id", ondelete="CASCADE"), index=True
    )
    meta_id: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(300))
    objective: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str | None] = mapped_column(String(30))
    effective_status: Mapped[str | None] = mapped_column(String(30))
    daily_budget: Mapped[float | None] = mapped_column(Float)
    lifetime_budget: Mapped[float | None] = mapped_column(Float)
    start_time: Mapped[datetime | None] = mapped_column(DateTime)
    stop_time: Mapped[datetime | None] = mapped_column(DateTime)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AdSet(Base):
    __tablename__ = "ad_sets"
    __table_args__ = (UniqueConstraint("workspace_id", "meta_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("ad_campaigns.id", ondelete="CASCADE"), index=True
    )
    meta_id: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(300))
    status: Mapped[str | None] = mapped_column(String(30))
    effective_status: Mapped[str | None] = mapped_column(String(30))
    optimization_goal: Mapped[str | None] = mapped_column(String(60))
    destination_type: Mapped[str | None] = mapped_column(String(60))
    daily_budget: Mapped[float | None] = mapped_column(Float)
    lifetime_budget: Mapped[float | None] = mapped_column(Float)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Ad(Base):
    __tablename__ = "ads"
    __table_args__ = (UniqueConstraint("workspace_id", "meta_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("ad_campaigns.id", ondelete="CASCADE"), index=True
    )
    ad_set_id: Mapped[int] = mapped_column(ForeignKey("ad_sets.id", ondelete="CASCADE"), index=True)
    meta_id: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(300))
    status: Mapped[str | None] = mapped_column(String(30))
    effective_status: Mapped[str | None] = mapped_column(String(30))
    review_feedback: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    thumbnail_url: Mapped[str | None] = mapped_column(String(1000))
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AdDailyMetric(Base):
    """Métricas de un anuncio en un día. Inversión, impresiones, clics y mensajes se suman;
    el alcance NO (son personas únicas): para períodos se usa AdPeriodMetric."""

    __tablename__ = "ad_daily_metrics"
    __table_args__ = (UniqueConstraint("ad_id", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    ad_id: Mapped[int] = mapped_column(ForeignKey("ads.id", ondelete="CASCADE"))
    ad_set_id: Mapped[int] = mapped_column(ForeignKey("ad_sets.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("ad_campaigns.id", ondelete="CASCADE"), index=True
    )
    day: Mapped[datetime] = mapped_column(DateTime, index=True)
    spend: Mapped[float] = mapped_column(Float, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    reach: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    link_clicks: Mapped[int] = mapped_column(Integer, default=0)
    messages: Mapped[int] = mapped_column(Integer, default=0)
    actions: Mapped[list[Any] | None] = mapped_column(JSON)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AdPeriodMetric(Base):
    """Alcance y frecuencia pedidos a Meta para un período exacto (no se pueden sumar días)."""

    __tablename__ = "ad_period_metrics"
    __table_args__ = (UniqueConstraint("workspace_id", "level", "meta_id", "since", "until"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    level: Mapped[str] = mapped_column(String(20))  # account | campaign | adset | ad
    meta_id: Mapped[str] = mapped_column(String(40))
    since: Mapped[datetime] = mapped_column(DateTime)
    until: Mapped[datetime] = mapped_column(DateTime)
    reach: Mapped[int] = mapped_column(Integer, default=0)
    frequency: Mapped[float | None] = mapped_column(Float)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    spend: Mapped[float] = mapped_column(Float, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AdChecklistItem(Base):
    __tablename__ = "ad_checklist_items"
    __table_args__ = (UniqueConstraint("workspace_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    key: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="pendiente")
    notes: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AdAlert(Base):
    """Aviso de la cuenta. `dedupe_key` evita repetir la misma alerta mientras siga abierta."""

    __tablename__ = "ad_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    severity: Mapped[str] = mapped_column(String(10), default="aviso")
    dedupe_key: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="abierta", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)


class AdReport(Base):
    """Reporte mensual. `data` es una foto congelada: no cambia si después se re-sincroniza."""

    __tablename__ = "ad_reports"
    __table_args__ = (UniqueConstraint("workspace_id", "month"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    month: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    pdf_path: Mapped[str | None] = mapped_column(String(500))
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class InstagramDaily(Base):
    """Métricas de la cuenta de Instagram de un solo día (para filtrar por semana, mes o año)."""

    __tablename__ = "instagram_daily"
    __table_args__ = (UniqueConstraint("workspace_id", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    day: Mapped[datetime] = mapped_column(DateTime)
    reach: Mapped[int | None] = mapped_column(Integer)
    views: Mapped[int | None] = mapped_column(Integer)
    profile_views: Mapped[int | None] = mapped_column(Integer)
    website_clicks: Mapped[int | None] = mapped_column(Integer)
    accounts_engaged: Mapped[int | None] = mapped_column(Integer)
    total_interactions: Mapped[int | None] = mapped_column(Integer)
    link_taps: Mapped[int | None] = mapped_column(Integer)
    # Toques a los botones del perfil por tipo: {"CALL": 2, "DIRECTION": 1, ...}
    link_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Advice(Base):
    """Último análisis pedido para una pantalla y unos filtros (Publicidad o Instagram)."""

    __tablename__ = "advice"
    __table_args__ = (UniqueConstraint("workspace_id", "scope", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    scope: Mapped[str] = mapped_column(String(20))  # publicidad | instagram
    key: Mapped[str] = mapped_column(String(200))  # filtros: período, campaña, formato
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
