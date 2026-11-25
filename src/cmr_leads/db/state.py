"""Estado chico de la app guardado en la base (antes eran archivos JSON en data/).

Si todavía existe el archivo viejo y la base no tiene nada, se toma del archivo una vez.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from cmr_leads.db.models import AppState


class DbState:
    def __init__(self, session: Session, workspace_id: int, key: str, legacy: Path | None = None):
        self.session, self.workspace_id, self.key, self.legacy = session, workspace_id, key, legacy

    def _row(self) -> AppState | None:
        return self.session.scalar(
            select(AppState).where(
                AppState.workspace_id == self.workspace_id, AppState.key == self.key
            )
        )

    def load(self) -> dict:
        row = self._row()
        if row is not None and isinstance(row.value, dict):
            return dict(row.value)
        if self.legacy is not None:
            try:
                return json.loads(self.legacy.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return {}

    def save(self, value: dict) -> None:
        row = self._row()
        if row is None:
            row = AppState(workspace_id=self.workspace_id, key=self.key)
            self.session.add(row)
        row.value = dict(value)
        self.session.flush()
