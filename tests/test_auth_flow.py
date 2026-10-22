"""OAuth authorize URL, state check, and token-form exchange."""

from __future__ import annotations

import httpx
import pytest
import respx

from tesla_mcp.auth_flow import (
    CallbackServer,
    build_authorize_url,
    consume_oauth_state,
    exchange_authorization_code,
    exchange_refresh_token,
    finish_login,
    parse_callback_input,
    write_oauth_state,
)
from tesla_mcp.errors import TeslaAuthError
from tesla_mcp.scopes import BASE_SCOPES
from tests.helpers import TOKEN_URL, make_jwt, make_settings


def test_authorize_url_requests_read_scopes_only(tmp_path):
    settings = make_settings(tmp_path)
    url = build_authorize_url(settings, "state-value")
    assert url.startswith("https://auth.test/authorize?")
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8765%2Fcallback" in url
    for scope in BASE_SCOPES:
        assert scope in url
    assert "vehicle_location" not in url
    assert "vehicle_charging_cmds" not in url
    assert "vehicle_cmds" not in url
    assert "user_data" not in url
    assert "show_keypair_step" not in url
    assert "prompt_missing_scopes=true" in url


def test_opt_in_scopes_are_added_only_when_enabled(tmp_path):
    settings = make_settings(tmp_path, enable_location=True, enable_charging_history=True)
    url = build_authorize_url(settings, "state-value")
    assert "vehicle_location" in url
    assert "vehicle_charging_cmds" in url
    assert "vehicle_cmds" not in url


def test_state_mismatch_does_not_exchange(tmp_path):
    settings = make_settings(tmp_path)
    write_oauth_state(settings, "expected-state")

    async def exchange(_code: str) -> dict[str, object]:
        raise AssertionError("exchange should not run")

    with pytest.raises(TeslaAuthError, match="state"):
        import asyncio

        asyncio.run(finish_login(settings, "code-value", "other-state", exchange=exchange))
    assert not settings.token_path.exists()


async def test_finish_login_saves_encrypted_tokens(tmp_path):
    settings = make_settings(tmp_path)
    write_oauth_state(settings, "expected-state")
    access = make_jwt(["openid", "offline_access", "vehicle_device_data"])

    async def exchange(code: str) -> dict[str, object]:
        assert code == "code-value"
        return {"access_token": access, "refresh_token": "refresh-one", "expires_in": 28800}

    record = await finish_login(settings, "code-value", "expected-state", exchange=exchange)
    assert record.refresh_token == "refresh-one"
    assert settings.token_path.is_file()
    assert b"refresh-one" not in settings.token_path.read_bytes()
    assert not settings.state_path.exists()


def test_parse_callback_url():
    code, state = parse_callback_input("https://pages.example/callback.html?code=abc&state=xyz")
    assert code == "abc"
    assert state == "xyz"


async def test_callback_server_does_not_log_the_code(capsys):
    server = CallbackServer("127.0.0.1", 0)
    server.start()
    try:
        httpx.get(
            f"http://127.0.0.1:{server.port}/callback",
            params={"code": "super-secret-code", "state": "state-123"},
        )
        code, state = server.wait(timeout=2)
    finally:
        server.close()
    captured = capsys.readouterr()
    assert code == "super-secret-code"
    assert state == "state-123"
    assert "super-secret-code" not in captured.out
    assert "super-secret-code" not in captured.err


async def test_code_exchange_posts_form_fields(tmp_path):
    settings = make_settings(tmp_path)
    with respx.mock(assert_all_called=True) as router:
        route = router.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={"access_token": "access", "refresh_token": "refresh", "expires_in": 28800},
            )
        )
        await exchange_authorization_code(settings, "the-code")
    body = route.calls[0].request.content.decode()
    assert "grant_type=authorization_code" in body
    assert "client_secret=test-client-secret" in body
    assert "code=the-code" in body
    assert "audience=https%3A%2F%2Ffleet.test" in body or "audience=https://fleet.test" in body
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8765%2Fcallback" in body or (
        "redirect_uri=http://localhost:8765/callback" in body
    )


async def test_refresh_form_omits_client_secret(tmp_path, caplog):
    settings = make_settings(tmp_path)
    caplog.set_level("INFO")
    with respx.mock(assert_all_called=True) as router:
        route = router.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={"access_token": "access", "refresh_token": "next", "expires_in": 28800},
            )
        )
        await exchange_refresh_token(settings, "refresh-token-test")
    body = route.calls[0].request.content.decode()
    assert "grant_type=refresh_token" in body
    assert "refresh_token=refresh-token-test" in body
    assert "client_secret" not in body
    assert "refresh-token-test" not in caplog.text


def test_consume_state_rejects_replay(tmp_path):
    settings = make_settings(tmp_path)
    write_oauth_state(settings, "once")
    consume_oauth_state(settings, "once")
    with pytest.raises(TeslaAuthError):
        consume_oauth_state(settings, "once")
