from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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
    display_name: Mapped[str | None] = mapped_column(String(200))
    bio: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(120))
    business_type: Mapped[str | None] = mapped_column(String(60))

    city: Mapped[str | None] = mapped_column(String(120))
    province: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(2))
    address: Mapped[str | None] = mapped_column(String(300))

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


class ApiUsage(Base):
    """Una fila por llamada a una API externa con cupo (para topes diarios)."""

    __tablename__ = "api_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    provider: Mapped[str] = mapped_column(String(40))
    used_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
