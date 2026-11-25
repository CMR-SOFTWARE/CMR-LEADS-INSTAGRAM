"""Prueba el almacenamiento de Supabase para publicar en Instagram.

Sube una imagen chiquita al bucket, verifica que se pueda ver por link público y la borra.
Uso (CMD, desde la carpeta cmr-leads):  .venv\\Scripts\\python scripts\\probar_supabase.py
"""

import io
import secrets
import tempfile
from pathlib import Path

import httpx
from PIL import Image

from cmr_leads.instagram.publish import SupabaseStorage
from cmr_leads.settings import Settings


def main() -> None:
    settings = Settings()
    if not settings.supabase_url or not settings.supabase_service_key:
        print("Falta SUPABASE_URL o SUPABASE_SERVICE_KEY en el archivo .env")
        return
    storage = SupabaseStorage(
        settings.supabase_url, settings.supabase_service_key, settings.supabase_bucket
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "prueba.jpg"
        buffer = io.BytesIO()
        Image.new("RGB", (32, 32), (10, 31, 68)).save(buffer, "JPEG")
        path.write_bytes(buffer.getvalue())
        key = f"prueba-{secrets.token_hex(6)}/prueba.jpg"
        url = storage.upload(path, key)
        print("1. Subida: OK")
        status = httpx.get(url, timeout=30).status_code
        print(f"2. Link público: {'OK' if status == 200 else f'falló (código {status})'}")
        storage.delete([key])
        # Se pregunta al almacenamiento y no al link público: ese tiene caché unos minutos.
        private = url.replace("/object/public/", "/object/authenticated/")
        gone = httpx.get(private, headers=storage._headers, timeout=30).status_code != 200
        print(f"3. Borrado: {'OK' if gone else 'quedó el archivo, revisar'}")


if __name__ == "__main__":
    main()
