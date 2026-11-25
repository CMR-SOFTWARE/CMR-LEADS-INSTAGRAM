"""Copia los datos de la base local (SQLite) a Supabase (schema cmr_leads).

Uso (CMD, desde la carpeta cmr-leads), con SUPABASE_DATABASE_URL en el .env:
    .venv\\Scripts\\python scripts\\mudar_a_supabase.py

- No borra nada de la base local.
- Si en Supabase ya hay datos, no copia (para no duplicar), salvo que pases --reemplazar.
- Al final muestra cuántas filas quedaron en cada tabla de los dos lados.
"""

import sys

from sqlalchemy import create_engine, func, select, text

from cmr_leads.db.models import Base
from cmr_leads.db.session import make_engine
from cmr_leads.settings import Settings

SOURCE = "sqlite:///data/cmr_leads.db"
BATCH = 500


def main() -> None:
    settings = Settings()
    target_url = settings.supabase_database_url
    if not target_url:
        print("Falta SUPABASE_DATABASE_URL en el .env (la cadena de conexión de Supabase).")
        return
    replace = "--reemplazar" in sys.argv
    source = create_engine(SOURCE)
    target = make_engine(target_url, settings.database_schema)
    tables = [t for t in Base.metadata.sorted_tables]
    with source.connect() as src, target.begin() as dst:
        has_data = [t.name for t in tables if dst.scalar(select(func.count()).select_from(t))]
        if has_data and not replace:
            print("Supabase ya tiene datos en:", ", ".join(has_data))
            print("No copié nada. Si querés pisarlos, corré con --reemplazar.")
            return
        if has_data:
            for table in reversed(tables):
                dst.execute(table.delete())
        for table in tables:
            rows = [dict(r._mapping) for r in src.execute(select(table))]
            for i in range(0, len(rows), BATCH):
                dst.execute(table.insert(), rows[i : i + BATCH])
            if rows and "id" in table.c:
                dst.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('{settings.database_schema}."
                        f"{table.name}', 'id'), (SELECT MAX(id) FROM {table.name}))"
                    )
                )
            print(f"{table.name}: {len(rows)} filas")
    with source.connect() as src, target.connect() as dst:
        bad = []
        for table in tables:
            a = src.scalar(select(func.count()).select_from(table))
            b = dst.scalar(select(func.count()).select_from(table))
            if a != b:
                bad.append(f"{table.name} (local {a}, Supabase {b})")
        print("Verificación: todo igual." if not bad else "Diferencias: " + "; ".join(bad))


if __name__ == "__main__":
    main()
