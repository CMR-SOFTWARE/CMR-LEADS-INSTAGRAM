"""Atajo: `python main.py <comando>` equivale a `cmr-leads <comando>`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from cmr_leads.cli import app  # noqa: E402

if __name__ == "__main__":
    app()
