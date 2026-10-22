"""Refresh-token rotation: persist the new refresh token before the access token is used."""

from __future__ import annotations

import os
import stat
import time

import pytest

from tesla_mcp.errors import TeslaAuthError, TokenPersistError
from tesla_mcp.token_store import TokenStore
from tests.helpers import seed_token


async def test_fresh_token_is_not_refreshed(tmp_path):
    settings = _settings(tmp_path)
    seed_token(settings, expires_at=time.time() + 3600)
    calls: list[str] = []

    async def exchange(refresh_token: str) -> dict[str, object]:
        calls.append(refresh_token)
        return {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 28800}

    store = TokenStore(settings, exchange=exchange)
    token = await store.get_access_token()
    assert calls == []
    assert token.startswith("ey")


async def test_refresh_persists_new_refresh_token_before_returning_access_token(tmp_path):
    settings = _settings(tmp_path)
    seed_token(settings, refresh="old-refresh", expires_at=time.time() + 10)
    seen: list[str] = []

    async def exchange(refresh_token: str) -> dict[str, object]:
        seen.append(refresh_token)
        return {
            "access_token": "access-after-write",
            "refresh_token": "refresh-after-write",
            "expires_in": 28800,
        }

    store = TokenStore(settings, exchange=exchange)

    def before_replace() -> None:
        current = store._read_unlocked()
        assert current is not None
        assert current.refresh_token == "old-refresh"

    store._before_replace = before_replace
    returned = await store.get_access_token()
    stored = store.load()
    assert stored is not None
    assert seen == ["old-refresh"]
    assert stored.refresh_token == "refresh-after-write"
    assert stored.previous_refresh_token == "old-refresh"
    assert returned == stored.access_token == "access-after-write"
    blob = settings.token_path.read_bytes()
    assert b"refresh-after-write" not in blob
    assert b"access-after-write" not in blob
    assert stat.S_IMODE(settings.token_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(settings.data_dir.stat().st_mode) == 0o700


async def test_failed_write_retries_with_the_presented_refresh_token(tmp_path):
    settings = _settings(tmp_path)
    seed_token(settings, refresh="old-refresh", expires_at=time.time() + 10)
    seen: list[str] = []
    attempts = {"n": 0}

    async def exchange(refresh_token: str) -> dict[str, object]:
        seen.append(refresh_token)
        index = len(seen)
        return {
            "access_token": f"access-{index}",
            "refresh_token": f"refresh-{index}",
            "expires_in": 28800,
        }

    store = TokenStore(settings, exchange=exchange)

    def before_replace() -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError("simulated crash before rename")

    store._before_replace = before_replace
    returned = await store.get_access_token()
    stored = store.load()
    assert stored is not None
    assert seen == ["old-refresh", "old-refresh"]
    assert stored.refresh_token == "refresh-2"
    assert returned == "access-2"
    assert not settings.token_path.with_name(settings.token_path.name + ".tmp").exists()


async def test_both_writes_fail_leaves_the_old_token_and_does_not_return_access(tmp_path):
    settings = _settings(tmp_path)
    seed_token(settings, refresh="old-refresh", expires_at=time.time() + 10)

    async def exchange(_refresh_token: str) -> dict[str, object]:
        return {
            "access_token": "unused-access",
            "refresh_token": "unused-refresh",
            "expires_in": 28800,
        }

    store = TokenStore(settings, exchange=exchange)
    store._before_replace = lambda: (_ for _ in ()).throw(OSError("disk full"))
    with pytest.raises(TokenPersistError):
        await store.get_access_token()
    stored = store.load()
    assert stored is not None
    assert stored.refresh_token == "old-refresh"
    assert stored.access_token != "unused-access"


def test_wrong_key_cannot_read_the_file(tmp_path):
    settings = _settings(tmp_path)
    seed_token(settings)
    other = _settings(tmp_path / "other", token_key=b"x" * 32)
    other.data_dir.mkdir(parents=True, exist_ok=True)
    other.token_path.write_bytes(settings.token_path.read_bytes())
    with pytest.raises(TeslaAuthError, match="TESLA_TOKEN_KEY"):
        TokenStore(other).load()


def test_file_lock_is_exclusive(tmp_path):
    import fcntl

    settings = _settings(tmp_path)
    store = TokenStore(settings)
    with store._file_lock():
        fd = os.open(settings.lock_path, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)


def _settings(tmp_path, **overrides):
    from tests.helpers import make_settings

    return make_settings(tmp_path, **overrides)
