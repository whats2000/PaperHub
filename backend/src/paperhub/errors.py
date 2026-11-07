"""Errors that carry a message meant for the end user."""
from __future__ import annotations


class UserFacingError(Exception):
    """An agent failure the chat should show as ``user_message`` (localized,
    free of raw validation dumps). ``str(exc)`` keeps the raw detail, which the
    tracer records on the failing ``tool_calls`` step."""

    def __init__(self, detail: str, *, user_message: str) -> None:
        super().__init__(detail)
        self.user_message = user_message
