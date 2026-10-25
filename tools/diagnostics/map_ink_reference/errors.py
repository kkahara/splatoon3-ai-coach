"""Fail-closed error types for the Amendment 3 reference tooling."""

from __future__ import annotations


class ReferenceProcedureError(RuntimeError):
    """Base error: the procedure must stop; nothing is silently repaired."""


class MalformedInput(ReferenceProcedureError):
    """An input is missing, malformed or internally inconsistent."""


class PinMismatch(ReferenceProcedureError):
    """A pinned artifact hash does not match (Amendment 3: STOP)."""


class SealViolation(ReferenceProcedureError):
    """A record required to be committed (sealed) first is not."""


class IsolationViolation(ReferenceProcedureError):
    """A blind stage received information it must never see."""


class ProcedureOrderError(ReferenceProcedureError):
    """An action was attempted out of the frozen procedure order."""


class ReproductionFailure(ReferenceProcedureError):
    """A sealed derivation could not be re-executed or gave different values."""


class UnresolvedAmbiguity(ReferenceProcedureError):
    """The sealed text does not determine this behavior; a ruling is required."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
