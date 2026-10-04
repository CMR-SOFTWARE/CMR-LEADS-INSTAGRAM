@echo off
REM Doble clic para abrir CMR Lead Finder en Windows.
cd /d "%~dp0"
if not exist .venv (
  echo Primera vez: instalando, tarda un par de minutos...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 (
    echo No se encontro Python. Instalalo desde https://www.python.org/downloads/ marcando "Add Python to PATH".
    pause
    exit /b 1
  )
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip -q
  pip install -e . -q
) else (
  call .venv\Scripts\activate.bat
)
if not exist .env copy .env.example .env >nul
cmr-leads web
pause
