from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cmr_leads.db.models import ApiUsage, utcnow


class DailyBudget:
    """Tope de llamadas por ventana móvil de 24 h, persistido en la base."""

    def __init__(self, session: Session, workspace_id: int, provider: str, limit: int) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.provider = provider
        self.limit = limit

    def window_start(self) -> datetime:
        return utcnow() - timedelta(hours=24)

    def used(self) -> int:
        since = self.window_start()
        return (
            self.session.scalar(
                select(func.count(ApiUsage.id)).where(
                    ApiUsage.workspace_id == self.workspace_id,
                    ApiUsage.provider == self.provider,
                    ApiUsage.used_at >= since,
                )
            )
            or 0
        )

    def remaining(self) -> int:
        return max(0, self.limit - self.used())

    def consume(self) -> None:
        self.session.add(ApiUsage(workspace_id=self.workspace_id, provider=self.provider))
        self.session.flush()


class MonthlyBudget(DailyBudget):
    """Tope por mes calendario: Google factura por mes y regala un cupo mensual por SKU."""

    def window_start(self) -> datetime:
        return utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
