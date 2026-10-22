"""Errors surfaced to tools and the CLI. Messages never include tokens or payloads."""


class TeslaError(Exception):
    """Base error for expected Tesla connector failures."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class TeslaAuthError(TeslaError):
    """Sign-in is missing, expired, or was revoked."""


class TeslaRegionError(TeslaError):
    """The configured Fleet API region does not own this account."""


class TeslaRateLimitError(TeslaError):
    """Tesla returned HTTP 429."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message, status_code=429)


class TeslaAsleepError(TeslaError):
    """The vehicle did not answer because it is asleep or offline."""


class TeslaConfigError(TeslaError):
    """Local configuration is missing or invalid."""


class TokenPersistError(TeslaError):
    """The new refresh token could not be saved. The access token must not be used."""


class WakeBudgetError(TeslaError):
    """A wake was refused by the local budget before any request was sent."""
