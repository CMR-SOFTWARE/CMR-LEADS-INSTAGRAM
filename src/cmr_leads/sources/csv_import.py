"""Fuente: CSV cargado a mano (relevamientos, ferias, referidos, etc.).

Columnas reconocidas (todas opcionales, al menos una de identificación):
instagram_username, display_name, bio, category, phone, whatsapp, email, website,
city, province, country, address, notes
"""

import csv
from collections.abc import Iterator
from pathlib import Path

from cmr_leads.domain.enums import SourceType
from cmr_leads.domain.schemas import RawProfile, SourceRecord

COLUMNS = (
    "instagram_username",
    "display_name",
    "bio",
    "category",
    "phone",
    "whatsapp",
    "email",
    "website",
    "city",
    "province",
    "country",
    "address",
    "notes",
)
ALIASES = {
    "instagram": "instagram_username",
    "username": "instagram_username",
    "nombre": "display_name",
    "telefono": "phone",
    "teléfono": "phone",
    "web": "website",
    "ciudad": "city",
    "notas": "notes",
}


class CsvSource:
    name = "csv"

    def __init__(self, path: Path) -> None:
        self.path = path

    def discover(self) -> Iterator[SourceRecord]:
        with open(self.path, newline="", encoding="utf-8-sig") as fh:
            sample = fh.read(4096)
            fh.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            for row in csv.DictReader(fh, dialect=dialect):
                values = {}
                for key, value in row.items():
                    if key is None:
                        continue
                    column = key.strip().lower()
                    column = ALIASES.get(column, column)
                    if column in COLUMNS and value and value.strip():
                        values[column] = value.strip()
                if not values:
                    continue
                yield SourceRecord(
                    profile=RawProfile(**values),
                    source=SourceType.CSV_IMPORT,
                    source_ref=self.path.name,
                )
