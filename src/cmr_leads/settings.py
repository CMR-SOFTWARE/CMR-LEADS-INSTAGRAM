import os
import re
import tempfile
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración leída de variables de entorno / `.env`. Nunca credenciales en el código."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///data/cmr_leads.db"
    database_schema: str = "cmr_leads"  # schema propio en Supabase (Postgres)
    supabase_database_url: str | None = None  # solo para mudar los datos a Supabase
    # En la nube (Vercel): login obligatorio y tareas por llamadas programadas (pg_cron).
    login_required: bool | None = None  # None = solo en Vercel
    login_hashes: str | None = None  # "tomi:sha256,chipi:sha256"; vacío = se leen del gestor
    gestor_core_url: str = "https://cmrsoftwaresolutions.com/gestion/js/core.js"
    session_secret: str | None = None  # firma de la cookie de sesión (obligatoria en la nube)
    cron_secret: str | None = None  # clave que manda pg_cron para correr las tareas
    run_background: bool | None = None  # bucles en segundo plano; None = no en Vercel
    # Sitios desde donde se aceptan formularios además del propio (la web que reenvía a /leads).
    trusted_origins: str = "cmrsoftwaresolutions.com,www.cmrsoftwaresolutions.com"
    base_path: str | None = None  # ruta debajo de la web (ej: /leads); None = /leads en Vercel
    workspace: str = "cmr"
    default_country: str = "AR"
    scoring_config: Path = Path("config/scoring.yaml")
    catalog_path: Path = Path("config/catalogo.md")
    sales_config: Path = Path("config/ventas.yaml")
    templates_path: Path = Path("config/plantillas.yaml")
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
    # Revisión automática de DMs mientras la app está abierta (0 = solo con el botón).
    instagram_dm_check_minutes: int = 30
    instagram_dm_state: Path = Path("data/instagram_dms.json")
    instagram_posts_to_sync: int = 25
    instagram_stats_hours: int = 6  # cada cuántas horas se actualizan días y publicaciones solos
    instagram_stories_minutes: int = 60  # cada cuánto leer las historias activas (0 = nunca)
    meta_max_stats_requests_per_day: int = 600
    # Cupo aparte para las historias: Meta las borra a las 24 h y no pueden quedarse sin consultas.
    meta_max_stories_requests_per_day: int = 300
    # Publicar desde la app: Meta baja las fotos y videos desde un link público, así que
    # antes de publicar se suben a un bucket público de Supabase Storage y después se borran.
    supabase_url: str | None = None  # https://xxxx.supabase.co
    supabase_service_key: str | None = None  # clave service_role (solo en .env, nunca en el código)
    supabase_bucket: str = "instagram"
    instagram_publish_dir: Path = Path("data/programadas")
    # Si la compu estaba apagada y pasaron más de estos minutos, no publica sola: pregunta.
    instagram_publish_grace_minutes: int = 30

    # Publicidad (Meta Ads). Token de un usuario del sistema de la app de anuncios
    # (permisos ads_read y business_management). Va aparte del token de Instagram.
    meta_ads_access_token: str | None = None
    meta_ad_account_id: str | None = None  # act_123... (también acepta solo el número)
    meta_ads_graph_api_version: str = "v23.0"
    # Sincronización automática mientras la app está abierta (0 = solo con el botón).
    meta_ads_sync_hours: int = 6
    meta_ads_max_requests_per_day: int = 600
    # Alerta si el costo por mensaje de una campaña sube más que este % (7 días contra 7 días).
    meta_ads_cost_alert_pct: float = 30.0
    # Alerta si al límite de gasto de la cuenta le queda menos que este monto (ARS).
    meta_ads_low_balance_ars: float = 20000.0
    meta_ads_reports_dir: Path = Path("data/reportes_publicidad")

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
    gemini_model: str = "gemini-3.5-flash"

    # Alternativa a Gemini para clasificar respuestas (opcional). Sin ninguna, se usan reglas.
    anthropic_api_key: str | None = None
    ai_model: str = "claude-opus-5-5"

    sender_name: str = "Tomás"
    company_name: str = "CMR Software Solutions"
    company_city: str = "San Nicolás"

    @field_validator("meta_graph_api_version", "meta_ads_graph_api_version")
    @classmethod
    def _graph_version(cls, value: str) -> str:
        """Acepta "v26", "v26." o "26" y lo deja como "v26.0" (Meta rechaza el resto)."""
        match = re.fullmatch(r"v?(\d+)(?:\.(\d*))?", (value or "").strip())
        if not match:
            return value
        return f"v{match.group(1)}.{match.group(2) or 0}"

    @model_validator(mode="after")
    def _cloud_paths(self):
        """En Vercel solo se puede escribir en /tmp (y se borra): lo que tiene que durar va a la
        base o a Supabase Storage; acá van solo archivos temporales."""
        if self.on_vercel:
            tmp = Path(tempfile.gettempdir()) / "cmr_leads"
            self.backup_dir = tmp / "backups"
            self.instagram_publish_dir = tmp / "programadas"
            self.meta_ads_reports_dir = tmp / "reportes_publicidad"
            self.instagram_dm_state = tmp / "instagram_dms.json"
            self.auto_search_state = tmp / "auto_search.json"
        return self

    @property
    def url_prefix(self) -> str:
        value = (
            self.base_path if self.base_path is not None else ("/leads" if self.on_vercel else "")
        )
        value = value.strip().strip("/")
        return f"/{value}" if value else ""

    @property
    def on_vercel(self) -> bool:
        return bool(os.environ.get("VERCEL"))

    @property
    def needs_login(self) -> bool:
        return self.on_vercel if self.login_required is None else self.login_required

    @property
    def background_enabled(self) -> bool:
        return (not self.on_vercel) if self.run_background is None else self.run_background

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

    @property
    def instagram_publish_configured(self) -> bool:
        return bool(self.instagram_configured and self.supabase_url and self.supabase_service_key)

    @property
    def ads_configured(self) -> bool:
        return bool(self.meta_ads_access_token and self.meta_ad_account_id)

    @property
    def ad_account_id(self) -> str | None:
        """Siempre con el prefijo act_, como lo pide la API."""
        raw = (self.meta_ad_account_id or "").strip()
        if not raw:
            return None
        return raw if raw.startswith("act_") else f"act_{raw}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
