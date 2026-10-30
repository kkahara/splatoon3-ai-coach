"""F11 command bodies. Media and calibration execution stay unauthorized."""

from __future__ import annotations

from pathlib import Path

from map_ink_reference.errors import ProcedureOrderError
from map_ink_reference.f11.eligibility import produce_step4
from map_ink_reference.seal import require_sealed

RECORDS_DIR = "analysis/review_validation/alignment/v4/amendment11"


def require_record(repo: Path, name: str) -> Path:
    """Return a sealed F11 record, or stop because this seal step has no record."""
    path = repo / RECORDS_DIR / name
    if not path.is_file():
        raise ProcedureOrderError(f"{name} has no sealed record")
    require_sealed(repo, path)
    return path


def eligibility_record(repo: Path) -> Path:
    """Write the two Step 4 artifacts. Invoking this remains a later authorization."""
    return produce_step4(repo)


def sampling_record(repo: Path) -> Path:
    """Sealing step 8 record. Absent until that step is separately authorized."""
    return require_record(repo, "sampling.json")
