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
    catalog_path: Path = Path("config/catalogo.md")
    sales_config: Path = Path("config/ventas.yaml")
    prices_config: Path = Path("config/precios.yaml")
    # Backup diario automático de la base (al abrir la app). Podés apuntarlo a Drive/OneDrive.
    backup_dir: Path = Path("data/backups")
    backup_keep_days: int = 30

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
    # Google Places es OPCIONAL: con False (o sin key) no se usa y solo queda OpenStreetMap.
    google_places_enabled: bool = True
    google_places_max_requests_per_day: int = 100
    # Google regala 1.000 consultas/mes de Text Search Enterprise; se deja margen para no pagar.
    google_places_max_requests_per_month: int = 900
    search_area: str = "San Nicolás de los Arroyos, Buenos Aires"
    # Al abrir la app busca sola unos rubros/localidades nuevos por día (1 consulta cada uno).
    auto_search_enabled: bool = True
    auto_search_per_day: int = 3
    # Cuántos leads nuevos analiza la IA por día para elegir qué ofrecerles.
    auto_offer_per_day: int = 15
    auto_search_state: Path = Path("data/auto_search.json")

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.8-flash"

    # Alternativa a Gemini para clasificar respuestas (opcional). Sin ninguna, se usan reglas.
    anthropic_api_key: str | None = None
    ai_model: str = "claude-opus-5-5"

    sender_name: str = "Tomás"
    company_name: str = "CMR Software Solutions"
    company_city: str = "San Nicolás"

    @property
    def reply_ai_configured(self) -> bool:
        """IA para clasificar respuestas de leads (Gemini o, si no, Claude)."""
        return bool(self.gemini_api_key or self.anthropic_api_key)

    @property
    def google_places_configured(self) -> bool:
        return self.google_places_enabled and bool(self.google_places_api_key)

    @property
    def ai_configured(self) -> bool:
        """IA para redactar mensajes (Gemini)."""
        return bool(self.gemini_api_key)

    @property
    def instagram_configured(self) -> bool:
        return bool(self.meta_access_token and self.instagram_business_account_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()
