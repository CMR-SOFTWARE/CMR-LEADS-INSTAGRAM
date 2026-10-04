"""Recorrido completo por la CLI sobre una base SQLite temporal (incluye migraciones)."""

from typer.testing import CliRunner

from cmr_leads import cli
from cmr_leads.settings import get_settings

from .conftest import FIXTURES, ROOT

runner = CliRunner()


def _invoke(*args, input=None):
    result = runner.invoke(cli.app, list(args), input=input)
    assert result.exit_code == 0, result.output
    return result.output


def test_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'leads.db'}")
    monkeypatch.setenv("SCORING_CONFIG", str(ROOT / "config" / "scoring.yaml"))
    monkeypatch.setenv("META_ACCESS_TOKEN", "")
    monkeypatch.setenv("WEBSITE_FETCH_ENABLED", "false")
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    try:
        _invoke("init")
        assert "3 nuevos" in _invoke(
            "import", "instagram-export", str(FIXTURES), "--account", "cmr.software"
        )
        _invoke(
            "add",
            "--name",
            "Estética Sofi",
            "--whatsapp",
            "3364-123456",
            "--city",
            "San Nicolás",
            "--note",
            "Turnos de estética",
        )
        _invoke("leads", "list")
        output = _invoke("leads", "show", "4")
        assert "Estética Sofi" in output

        # No se genera mensaje sin aprobación.
        result = runner.invoke(cli.app, ["message", "4"])
        assert result.exit_code == 1

        _invoke("status", "4", "approved")
        output = _invoke("message", "4")
        assert "wa.me/5493364123456" in output.replace("\n", "")
        _invoke("contacted", "4", "--channel", "whatsapp")
        _invoke("dnc", "1")
        assert runner.invoke(cli.app, ["message", "1", "--force"]).exit_code == 1

        _invoke("export", "--format", "xlsx", "--output", str(tmp_path / "out.xlsx"))
        assert (tmp_path / "out.xlsx").exists()
        output = _invoke("stats")
        assert "cmr_followers" in output and "manual" in output
    finally:
        get_settings.cache_clear()
