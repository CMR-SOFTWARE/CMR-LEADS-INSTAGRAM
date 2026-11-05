"""Backup diario de la base SQLite a una carpeta configurable (BACKUP_DIR en .env)."""

import sqlite3
from datetime import date
from pathlib import Path


def sqlite_path(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite:///") or ":memory:" in database_url:
        return None
    return Path(database_url.removeprefix("sqlite:///"))


def daily_backup(
    database_url: str, backup_dir: Path, keep: int = 30, today: date | None = None
) -> Path | None:
    """Copia la base una vez por día (usa la API de backup de SQLite: copia consistente aunque la
    app esté abierta). Conserva las últimas `keep` copias diarias. Devuelve la copia nueva o None."""
    source = sqlite_path(database_url)
    if source is None or not source.exists():
        return None
    today = today or date.today()
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"cmr_leads_{today.isoformat()}.db"
    if target.exists():
        return None
    src, dst = sqlite3.connect(source), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:  # `with sqlite3.connect()` no cierra la conexión: en Windows quedaría bloqueado
        dst.close()
        src.close()
    daily = sorted(backup_dir.glob("cmr_leads_????-??-??.db"))
    for old in daily[:-keep] if keep > 0 else []:
        old.unlink()
    return target
