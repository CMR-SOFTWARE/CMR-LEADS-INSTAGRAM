"""Fuente: export oficial de Instagram ("Descargar tu información" -> Seguidores y seguidos).

Acepta el .zip completo, una carpeta descomprimida o un .json suelto. Solo lee los archivos de
seguidores (`followers_*.json`) y seguidos (`following.json`).
"""

import json
import zipfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any

from cmr_leads.domain.enums import SourceType
from cmr_leads.domain.schemas import RawProfile, SourceRecord
from cmr_leads.pipeline.normalize import normalize_username


def classify_file(name: str) -> SourceType | None:
    base = PurePosixPath(name).name.lower()
    if not base.endswith(".json"):
        return None
    if base.startswith("followers"):
        return SourceType.CMR_FOLLOWERS
    if base.startswith("following"):
        return SourceType.CMR_FOLLOWING
    return None


def _walk_entries(data: Any) -> Iterator[dict]:
    """El formato cambió varias veces; buscamos cualquier objeto con `string_list_data`."""
    if isinstance(data, dict):
        if "string_list_data" in data:
            yield data
            return
        for value in data.values():
            yield from _walk_entries(value)
    elif isinstance(data, list):
        for item in data:
            yield from _walk_entries(item)


def usernames_from_json(data: Any) -> list[str]:
    found: list[str] = []
    for entry in _walk_entries(data):
        candidates = [entry.get("title")]
        for item in entry.get("string_list_data") or []:
            candidates += [item.get("value"), item.get("href")]
        for candidate in candidates:
            username = normalize_username(candidate)
            if username:
                if username not in found:
                    found.append(username)
                break
    return found


def _iter_json_files(path: Path) -> Iterator[tuple[str, Any]]:
    if path.is_dir():
        for file in sorted(path.rglob("*.json")):
            yield str(file), json.loads(file.read_text(encoding="utf-8"))
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                if classify_file(name):
                    yield name, json.loads(archive.read(name).decode("utf-8"))
    else:
        yield str(path), json.loads(path.read_text(encoding="utf-8"))


class InstagramExportSource:
    name = "instagram_export"

    def __init__(self, path: Path, account_username: str = "") -> None:
        self.path = path
        self.account_username = account_username

    def discover(self) -> Iterator[SourceRecord]:
        for filename, data in _iter_json_files(self.path):
            source = classify_file(filename)
            if source is None:
                continue
            for username in usernames_from_json(data):
                yield SourceRecord(
                    profile=RawProfile(instagram_username=username),
                    source=source,
                    source_ref=self.account_username,
                )
