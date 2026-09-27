class FactoryError(Exception):
    """Base exception rendered as a user-facing CLI error."""


class SpecError(FactoryError):
    """A rig or mission specification is invalid."""


class HermesCommandError(FactoryError):
    """A Hermes subprocess returned a non-zero status or invalid JSON."""

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class StaleRecoveryDecision(FactoryError):
    """A completed recovery plan no longer matches live authoritative Kanban state.

    The plan is rejected without mutation and disposed of in its own card record so a
    superseded decision cannot fail every later reconcile pass.
    """
