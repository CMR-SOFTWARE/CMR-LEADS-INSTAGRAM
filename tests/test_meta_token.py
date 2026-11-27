from datetime import UTC, datetime, timedelta

import httpx

from cmr_leads.meta_token import check_token, explain_auth_error


def test_expired_message_in_spanish():
    raw = (
        "Error validating access token: Session has expired on Tuesday, 25-Nov-26 "
        "10:00:00 PST. The current time is Thursday, 27-Nov-26 08:00:00 PST."
    )
    assert explain_auth_error(raw) == "El token de Meta venció el 25/11/2026. Hay que renovarlo."
    assert "contraseña" in explain_auth_error(
        "Error validating access token: The session has been invalidated because the user "
        "changed their password"
    )
    assert "no es válido" in explain_auth_error("Invalid OAuth access token - Cannot parse")


def _client(payload):
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))


def test_check_token_states():
    soon = int((datetime.now(UTC) + timedelta(days=3)).timestamp())
    far = int((datetime.now(UTC) + timedelta(days=50)).timestamp())
    expiring = check_token(
        "t", "v23.0", "Instagram", _client({"data": {"is_valid": True, "expires_at": soon}})
    )
    ok = check_token(
        "t", "v23.0", "Instagram", _client({"data": {"is_valid": True, "expires_at": far}})
    )
    never = check_token(
        "t", "v23.0", "Instagram", _client({"data": {"is_valid": True, "expires_at": 0}})
    )
    dead = check_token(
        "t",
        "v23.0",
        "Instagram",
        _client({"error": {"message": "Session has expired on Tuesday, 25-Nov-26 10:00:00 PST."}}),
    )
    assert expiring.needs_action and expiring.days_left in (2, 3)
    assert not ok.needs_action and not never.needs_action
    assert dead.valid is False and dead.needs_action and "25/11/2026" in dead.problem
