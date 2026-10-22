"""Encrypted token file.

Refresh tokens are single-use. The previous refresh token stays valid for 24
hours, so a new refresh token is written atomically (temp file, fsync, rename)
before the new access token is returned to any caller. A failed write retries
the exchange once with the refresh token that was just presented.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import time
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from .config import Settings
from .crypto import decrypt, encrypt
from .errors import TeslaAuthError, TokenPersistError
from .scopes import scopes_from_access_token

REFRESH_SKEW_SECONDS = 60
PREVIOUS_REFRESH_TTL_SECONDS = 24 * 60 * 60
ASSUMED_EXPIRES_IN = 8 * 60 * 60

Exchange = Callable[[str], Awaitable[dict[str, Any]]]


@dataclass
class TokenRecord:
    access_token: str
    refresh_token: str
    expires_at: float
    scopes: list[str]
    region: str
    previous_refresh_token: str | None = None
    previous_refresh_valid_until: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scopes": self.scopes,
            "region": self.region,
            "previous_refresh_token": self.previous_refresh_token,
            "previous_refresh_valid_until": self.previous_refresh_valid_until,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenRecord:
        scopes = data.get("scopes") or []
        if not isinstance(scopes, list):
            scopes = []
        return cls(
            access_token=str(data["access_token"]),
            refresh_token=str(data["refresh_token"]),
            expires_at=float(data["expires_at"]),
            scopes=[str(item) for item in scopes],
            region=str(data.get("region") or ""),
            previous_refresh_token=(
                str(data["previous_refresh_token"]) if data.get("previous_refresh_token") else None
            ),
            previous_refresh_valid_until=(
                float(data["previous_refresh_valid_until"])
                if data.get("previous_refresh_valid_until")
                else None
            ),
        )


class TokenStore:
    def __init__(self, settings: Settings, exchange: Exchange | None = None) -> None:
        self.settings = settings
        self._exchange = exchange
        self._async_lock = asyncio.Lock()
        self._before_replace: Callable[[], None] | None = None

    def _prepare_dir(self) -> None:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.settings.data_dir, 0o700)

    @contextmanager
    def _file_lock(self) -> Any:
        self._prepare_dir()
        fd = os.open(self.settings.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read_unlocked(self) -> TokenRecord | None:
        path = self.settings.token_path
        if not path.is_file():
            return None
        key = self.settings.require_token_key()
        try:
            plaintext = decrypt(key, path.read_bytes())
        except ValueError as exc:
            raise TeslaAuthError(str(exc)) from exc
        payload = json.loads(plaintext.decode("utf-8"))
        if not isinstance(payload, dict):
            raise TeslaAuthError("Token file did not contain a token record.")
        return TokenRecord.from_dict(payload)

    def _write_unlocked(self, record: TokenRecord) -> None:
        """Write the record atomically. Raises TokenPersistError before replace on failure."""
        key = self.settings.require_token_key()
        path = self.settings.token_path
        self._prepare_dir()
        blob = encrypt(key, json.dumps(record.to_dict()).encode("utf-8"))
        tmp = path.with_name(path.name + ".tmp")
        fd: int | None = None
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.write(fd, blob)
            os.fsync(fd)
            os.chmod(tmp, 0o600)
            if self._before_replace is not None:
                self._before_replace()
            os.replace(tmp, path)
            os.chmod(path, 0o600)
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except TokenPersistError:
            _unlink_quiet(tmp)
            raise
        except Exception as exc:
            _unlink_quiet(tmp)
            raise TokenPersistError(
                "Could not save the new refresh token. The new access token was not used. "
                "The previous refresh token remains valid for up to 24 hours."
            ) from exc
        finally:
            if fd is not None:
                os.close(fd)

    def load(self) -> TokenRecord | None:
        with self._file_lock():
            return self._read_unlocked()

    def save(self, record: TokenRecord) -> None:
        with self._file_lock():
            self._write_unlocked(record)

    def delete(self) -> bool:
        with self._file_lock():
            path = self.settings.token_path
            if not path.exists():
                return False
            path.unlink()
            return True

    async def get_access_token(self, *, force: bool = False) -> str:
        """Return an access token, refreshing first when it is inside the skew window.

        The access token from a refresh response is returned only after that
        response's refresh token has been read back from disk.
        """
        async with self._async_lock:
            with self._file_lock():
                record = self._read_unlocked()
                if record is None:
                    raise TeslaAuthError(
                        "No Tesla tokens stored. Run `tesla-mcp auth` on this computer."
                    )
                remaining = record.expires_at - time.time()
                if not force and remaining > REFRESH_SKEW_SECONDS:
                    return record.access_token
                await self._refresh_holding_lock(record)
                stored = self._read_unlocked()
                if stored is None:
                    raise TokenPersistError("Token file was empty after refresh.")
                return stored.access_token

    async def force_refresh(self) -> str:
        return await self.get_access_token(force=True)

    async def _refresh_holding_lock(self, record: TokenRecord) -> None:
        presented = record.refresh_token
        try:
            updated = await self._exchange_into_record(record, presented)
            self._write_unlocked(updated)
            return
        except TokenPersistError:
            pass
        # The refresh token just presented stays valid for 24 hours. Do not retry
        # with the new refresh token, because that token was never saved.
        updated = await self._exchange_into_record(record, presented)
        self._write_unlocked(updated)

    async def _exchange_into_record(self, current: TokenRecord, refresh_token: str) -> TokenRecord:
        body = await self._call_exchange(refresh_token)
        access = body.get("access_token")
        refresh = body.get("refresh_token")
        if not isinstance(access, str) or not access:
            raise TeslaAuthError(
                "Tesla did not return an access token. Run `tesla-mcp auth` again."
            )
        if not isinstance(refresh, str) or not refresh:
            raise TeslaAuthError(
                "Tesla did not return a new refresh token. Run `tesla-mcp auth` again. "
                "The previous refresh token was not replaced."
            )
        expires_in = body.get("expires_in")
        try:
            lifetime = float(expires_in) if expires_in is not None else float(ASSUMED_EXPIRES_IN)
        except (TypeError, ValueError):
            lifetime = float(ASSUMED_EXPIRES_IN)
        scopes = scopes_from_access_token(access)
        now = time.time()
        return TokenRecord(
            access_token=access,
            refresh_token=refresh,
            expires_at=now + lifetime,
            scopes=scopes if scopes is not None else list(current.scopes),
            region=self.settings.region,
            previous_refresh_token=refresh_token,
            previous_refresh_valid_until=now + PREVIOUS_REFRESH_TTL_SECONDS,
        )

    async def _call_exchange(self, refresh_token: str) -> dict[str, Any]:
        if self._exchange is not None:
            return await self._exchange(refresh_token)
        from .auth_flow import exchange_refresh_token

        return await exchange_refresh_token(self.settings, refresh_token)


def _unlink_quiet(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def restrict(path: Path) -> None:
    if path.exists():
        os.chmod(path, 0o600)


def open_private(path: Path) -> IO[str]:
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.chmod(path, 0o600)
    return os.fdopen(fd, "r+", encoding="utf-8")
