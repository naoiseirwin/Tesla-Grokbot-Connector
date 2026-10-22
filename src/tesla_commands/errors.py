"""Refusals from the commands connector. Messages never include tokens."""


class CommandRefusal(Exception):
    """A command was refused before Tesla was asked to do it."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        self.message = message
        super().__init__(message)
