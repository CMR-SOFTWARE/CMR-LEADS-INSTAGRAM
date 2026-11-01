from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración leída de variables de entorno / `.env`. Nunca credenciales en el código."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///data/cmr_leads.db"
    workspace: str = "cmr"
    default_country: str = "AR"
    scoring_config: Path = Path("config/scoring.yaml")

    meta_access_token: str | None = None
    instagram_business_account_id: str | None = None
    meta_graph_api_version: str = "v23.0"
    meta_max_lookups_per_day: int = 150
    # Para leer los mensajes directos (opcional): ID de la página de Facebook vinculada.
    facebook_page_id: str | None = None
    instagram_posts_to_sync: int = 25
    meta_max_stats_requests_per_day: int = 300

    website_fetch_enabled: bool = True
    website_timeout_seconds: float = 10.0

    google_places_api_key: str | None = None
    google_places_max_requests_per_day: int = 100
    search_area: str = "San Nicolás de los Arroyos, Buenos Aires"

    # IA (opcional) para clasificar respuestas. Sin clave se usan reglas.
    anthropic_api_key: str | None = None
    ai_model: str = "claude-opus-5-5"

    sender_name: str = "Tomás"
    company_name: str = "CMR Software Solutions"
    company_city: str = "San Nicolás"

    @property
    def ai_configured(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def google_places_configured(self) -> bool:
        return bool(self.google_places_api_key)

    @property
    def instagram_configured(self) -> bool:
        return bool(self.meta_access_token and self.instagram_business_account_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()
