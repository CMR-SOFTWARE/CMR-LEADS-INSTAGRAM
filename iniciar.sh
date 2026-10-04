#!/usr/bin/env bash
# Abre CMR Lead Finder en Mac/Linux: ./iniciar.sh
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Primera vez: instalando, tarda un par de minutos..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip -q
  .venv/bin/pip install -e . -q
fi
[ -f .env ] || cp .env.example .env
exec .venv/bin/cmr-leads web
