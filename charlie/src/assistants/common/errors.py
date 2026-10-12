"""Exception types shared across services."""


class AssistantsError(Exception):
    """Base class."""


class PolicyError(AssistantsError):
    """Policy file invalid or a tool is not in policy."""


class Denied(AssistantsError):
    """A request was denied by code (rules, executor, approvals)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ValidationFailed(AssistantsError):
    """Input did not match the fixed schema."""


class SecretMissing(AssistantsError):
    """A secret was requested that the store does not hold."""
