from typing import Any

from pydantic import BaseModel, Field

from cmr_leads.domain.enums import SourceType


class RawProfile(BaseModel):
    """Perfil tal como lo entrega una fuente, antes de normalizar."""

    instagram_username: str | None = None
    instagram_user_id: str | None = None
    display_name: str | None = None
    bio: str | None = None
    category: str | None = None
    website: str | None = None
    phone: str | None = None
    whatsapp: str | None = None
    email: str | None = None
    city: str | None = None
    province: str | None = None
    country: str | None = None
    address: str | None = None
    followers: int | None = None
    following: int | None = None
    posts_count: int | None = None
    is_business_account: bool | None = None
    notes: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class SourceRecord(BaseModel):
    """Un perfil descubierto más el origen del descubrimiento."""

    profile: RawProfile
    source: SourceType
    source_ref: str = ""
