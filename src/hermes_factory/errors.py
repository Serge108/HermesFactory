class FactoryError(Exception):
    """Base exception rendered as a user-facing CLI error."""


class SpecError(FactoryError):
    """A rig or mission specification is invalid."""


class HermesCommandError(FactoryError):
    """A Hermes subprocess returned a non-zero status or invalid JSON."""
