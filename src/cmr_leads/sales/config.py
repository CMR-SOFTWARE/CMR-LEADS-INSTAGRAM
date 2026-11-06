"""Configuración editable del embudo (config/ventas.yaml) y de precios (config/precios.yaml)."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class SalesConfig(BaseModel):
    follow_up_days: list[int] = Field(default_factory=lambda: [3, 7])
    close_after_days: int = 14
    stuck_days: dict[str, int] = Field(
        default_factory=lambda: {
            "approved": 3,
            "contacted": 14,
            "replied": 3,
            "meeting": 7,
            "proposal": 10,
        }
    )
    briefing_size: int = 8
    email_daily_limit: int = 20
    # Piloto automático (0 o false = apagado). Todo se puede deshacer desde "Mi jornada".
    auto_calificar_desde_ia: int = 75
    auto_calificar_desde_score: int = 60
    auto_calificar_max_por_dia: int = 15
    auto_cerrar_sin_respuesta: bool = True


class PlanVariant(BaseModel):
    nombre: str
    monthly: int = 0
    one_time: int = 0


class Plan(BaseModel):
    nombre: str
    monthly: int = 0
    one_time: int = 0
    incluye: list[str] = Field(default_factory=list)
    variantes: list[PlanVariant] = Field(default_factory=list)


class PriceList(BaseModel):
    planes: dict[str, Plan] = Field(default_factory=dict)
    extras: list[PlanVariant] = Field(default_factory=list)
    condiciones: str = ""


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_sales_config(path: Path) -> SalesConfig:
    return SalesConfig.model_validate(_load(path))


def load_prices(path: Path) -> PriceList:
    return PriceList.model_validate(_load(path))
