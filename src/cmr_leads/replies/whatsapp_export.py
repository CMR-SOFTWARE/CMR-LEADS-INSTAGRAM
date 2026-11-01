"""Lee el .txt de "Exportar chat" de WhatsApp (Android o iPhone, sin archivos)."""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePath

# Android: "18/10/26, 14:32 - Nombre: texto"  ·  iPhone: "[18/10/26, 14:32:05] Nombre: texto"
LINE_RE = re.compile(
    r"^‎?\[?(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),?\s+"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?)\s*(?P<ampm>[ap]\.?\s?m\.?)?\]?\s*(?:-\s*)?"
    r"(?P<sender>[^:]{1,80}?):\s(?P<text>.*)$",
    re.IGNORECASE,
)
FILENAME_RE = re.compile(
    r"(?:chat de whatsapp con|whatsapp chat with|whatsapp chat -)\s*(.+?)\.txt$", re.I
)
SYSTEM_HINTS = (
    "cifrados de extremo a extremo",
    "end-to-end encrypted",
    "<multimedia omitido>",
    "<media omitted>",
    "se eliminó este mensaje",
    "this message was deleted",
)


@dataclass
class ChatMessage:
    at: datetime
    sender: str
    text: str


def _parse_dt(date: str, time: str, ampm: str | None) -> datetime:
    day, month, year = (int(x) for x in date.split("/"))
    if year < 100:
        year += 2000
    parts = [int(x) for x in time.split(":")]
    hour, minute = parts[0], parts[1]
    second = parts[2] if len(parts) > 2 else 0
    if ampm:
        pm = ampm.lower().startswith("p")
        if pm and hour < 12:
            hour += 12
        if not pm and hour == 12:
            hour = 0
    return datetime(year, month, day, hour, minute, second)


def parse_chat(text: str) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        line = raw_line.replace(" ", " ").replace("\xa0", " ")
        match = LINE_RE.match(line)
        if match:
            try:
                at = _parse_dt(match["date"], match["time"], match["ampm"])
            except ValueError:
                continue
            messages.append(
                ChatMessage(at, match["sender"].strip().lstrip("‎"), match["text"].strip())
            )
        elif messages and line.strip():
            messages[-1].text += "\n" + line.strip()  # mensaje de varias líneas
    return [m for m in messages if not any(h in m.text.lower() for h in SYSTEM_HINTS)]


def contact_from_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    match = FILENAME_RE.search(PurePath(filename).name)
    return match.group(1).strip() if match else None


def split_sides(
    messages: list[ChatMessage], my_name: str | None, contact: str | None
) -> str | None:
    """Devuelve el nombre del remitente que soy yo, o None si no se puede saber."""
    senders = list(dict.fromkeys(m.sender for m in messages))
    if my_name:
        for sender in senders:
            if sender.lower() == my_name.lower():
                return sender
    if contact and len(senders) == 2:
        others = [s for s in senders if s.lower() != contact.lower()]
        if len(others) == 1:
            return others[0]
    return None
