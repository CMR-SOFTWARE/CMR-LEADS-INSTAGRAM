"""Entrada de Vercel: expone la app web (FastAPI) como `app`.

Las rutas de config/ son relativas a la carpeta del proyecto, por eso se fija el directorio.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

from cmr_leads.web.app import create_app  # noqa: E402

app = create_app()
