from __future__ import annotations


class GameplayError(Exception):
    """Base class for gameplay-related errors."""


class EngineValidationError(GameplayError):
    """Raised when a submitted action is invalid under server rules."""


class NotPlayersTurnError(EngineValidationError):
    """Raised when a player attempts an action out of turn."""


class MatchNotActiveError(EngineValidationError):
    """Raised when actions are submitted for a non-active match."""


class DegradedModeError(GameplayError):
    """Raised when required infrastructure is unavailable for an operation."""
