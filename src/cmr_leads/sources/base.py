from collections.abc import Iterator
from typing import Protocol

from cmr_leads.domain.schemas import SourceRecord


class LeadSource(Protocol):
    """Interfaz común de fuentes. Agregar una fuente nueva = una clase con `discover()`."""

    name: str

    def discover(self) -> Iterator[SourceRecord]: ...
