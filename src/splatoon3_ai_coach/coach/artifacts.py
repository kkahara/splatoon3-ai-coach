"""Canonical boundary for coach-inputs and coach-prototype artifacts on disk.

Every reader and writer of ``coach_inputs/`` and ``coach_prototype/`` goes
through this module: filenames, index schemas, typed loaders, the explicit
primary model/output, and relationship checks between documents.

Layout::

    coach_inputs/coaching_index.json        CoachingIndex (rank order)
    coach_inputs/{safe_id}.coach_input.json CoachInput
    coach_inputs/{safe_id}.coaching.json    CoachingUnitResult
    coach_inputs/{safe_id}.llm_view.json    CoachLlmView
    coach_prototype/coaching_index.json     PrototypeIndex
    coach_prototype/{safe_id}.{model_tag}.output.json  CoachingAssessment

Legacy compatibility (schema version 1): index files written as a bare JSON
list are still read. A legacy coach-prototype unit names its primary output
only when exactly one ``*.output.json`` exists for it; with several, the
primary is reported as ambiguous and :func:`primary_assessment` raises
:class:`ArtifactError` asking for ``coach-prototype`` to be re-run. Nothing
outside this module reads the legacy layout.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from splatoon3_ai_coach.coach.claim_catalog import CoachingUnitResult
from splatoon3_ai_coach.coach.coach_input import CoachInput
from splatoon3_ai_coach.coach.llm_client import CoachingAssessment, GenerationSetting
from splatoon3_ai_coach.coach.llm_view import CoachLlmView
from splatoon3_ai_coach.exceptions import S3CoachError

COACH_INPUTS_DIRNAME = "coach_inputs"
COACH_PROTOTYPE_DIRNAME = "coach_prototype"
COACH_INPUTS_META_FILENAME = "coach_inputs_meta.json"
COACHING_INDEX_FILENAME = "coaching_index.json"
SYSTEM_PROMPT_FILENAME = "system_prompt.txt"

LEGACY_SCHEMA_VERSION = 1
COACHING_INDEX_SCHEMA_VERSION = 3
# Bump when deterministic evidence semantics change (coaching.json / llm_view).
# v4: roster capability off, same-life special, claim_basis / undetermined.
# v5: claim_basis evidence_paths are literal scalar CoachLlmView leaves.
COACH_EVIDENCE_VERSION = 5
VISION_MANIFEST_FILENAME = "vision_manifest.json"
PROTOTYPE_INDEX_SCHEMA_VERSION = 3

OUTPUT_SUFFIX = ".output.json"
RAW_SUFFIX = ".raw.txt"
FLAGS_SUFFIX = ".flags.json"
_COACH_INPUT_SUFFIX = ".coach_input.json"

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class ArtifactError(S3CoachError):
    """Coach artifacts are missing, malformed, inconsistent, or ambiguous."""


def safe_filename(value: str) -> str:
    """Filesystem-safe token for scenario/model names."""
    return re.sub(r"[^\w.\-]+", "_", value)


def _bare_filename(name: str) -> str:
    if not name or name in {".", ".."} or name != Path(name).name:
        raise ValueError(f"artifact name must be a bare filename: {name!r}")
    return name


BareName = Annotated[str, AfterValidator(_bare_filename)]


@dataclass(frozen=True)
class UnitFileNames:
    """Per-unit document filenames shared by coach-inputs and coach-prototype."""

    coach_input: str
    coaching: str
    llm_view: str
    user_prompt: str
    vmv_player: str
    vmv_dev: str


def unit_file_names(safe_id: str) -> UnitFileNames:
    """Filenames for one coaching unit."""
    return UnitFileNames(
        coach_input=f"{safe_id}{_COACH_INPUT_SUFFIX}",
        coaching=f"{safe_id}.coaching.json",
        llm_view=f"{safe_id}.llm_view.json",
        user_prompt=f"{safe_id}.user_prompt.txt",
        vmv_player=f"{safe_id}.vmv.player.txt",
        vmv_dev=f"{safe_id}.vmv.dev.txt",
    )


@dataclass(frozen=True)
class OutputFileNames:
    """Assessment, raw reply, and flag annotation filenames for one model run."""

    output: str
    raw: str
    flags: str


def output_file_names(safe_id: str, model_label: str) -> OutputFileNames:
    """Filenames for one unit's run of ``model_label``."""
    stem = f"{safe_id}.{safe_filename(model_label)}"
    return OutputFileNames(
        output=f"{stem}{OUTPUT_SUFFIX}",
        raw=f"{stem}{RAW_SUFFIX}",
        flags=f"{stem}{FLAGS_SUFFIX}",
    )


class CoachingIndexEntry(BaseModel):
    """One ranked unit in ``coach_inputs/coaching_index.json``."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    candidate_id: str
    candidate_type: str
    safe_id: BareName
    video_time: float = Field(ge=0)
    importance_score: float
    rank: int | None = None
    selected_for_llm: bool = False
    importance_config_hash: str | None = None
    active_factors: list[str] = Field(default_factory=list)
    coach_input_json: BareName
    coaching_json: BareName
    llm_view_json: BareName | None = None
    vmv_player: BareName | None = None
    vmv_dev: BareName | None = None


class CoachingIndex(BaseModel):
    """Rank-ordered coach-inputs index."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = COACHING_INDEX_SCHEMA_VERSION
    evidence_version: int | None = Field(
        default=None, description="None for artifacts written before versioning."
    )
    analysis_id: str | None = None
    entries: list[CoachingIndexEntry]

    def by_scenario_id(self, scenario_id: str) -> CoachingIndexEntry | None:
        """Entry for ``scenario_id``, if indexed."""
        return next((e for e in self.entries if e.scenario_id == scenario_id), None)


class RunProvenance(BaseModel):
    """What one LLM call actually received; replay is best effort, not exact."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    temperature: GenerationSetting
    seed: GenerationSetting
    input_hashes: dict[str, str]
    llm_view_sha256: str | None = None
    importance_config_hash: str | None = None
    executed_at: str
    determinism: Literal["best_effort"] = "best_effort"


class PrototypeOutput(BaseModel):
    """One model run for one unit in coach-prototype."""

    model_config = ConfigDict(extra="forbid")

    model: str
    output_json: BareName
    raw_txt: BareName
    flags_json: BareName
    skipped_llm: bool | None = Field(
        default=None, description="None only for legacy artifacts (not recorded)."
    )
    provenance: RunProvenance | None = Field(
        default=None,
        description="None for skipped runs and schema <= 2 artifacts (not recorded).",
    )


class PrototypeIndexEntry(BaseModel):
    """One unit in ``coach_prototype/coaching_index.json``."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    candidate_id: str
    candidate_type: str
    safe_id: BareName
    video_time: float = Field(ge=0)
    importance_score: float
    rank: int | None = None
    selected_for_llm: bool = False
    active_factors: list[str] = Field(default_factory=list)
    coach_input_json: BareName
    coaching_json: BareName
    llm_view_json: BareName | None = None
    user_prompt_txt: BareName | None = None
    outputs: list[PrototypeOutput] = Field(default_factory=list)
    primary_output_json: BareName | None = None
    primary_ambiguous: bool = False

    @model_validator(mode="after")
    def _primary_is_an_output(self) -> PrototypeIndexEntry:
        names = {o.output_json for o in self.outputs}
        if self.primary_output_json is not None and self.primary_output_json not in names:
            raise ValueError(f"primary output {self.primary_output_json} is not listed")
        if self.primary_ambiguous and self.primary_output_json is not None:
            raise ValueError("an ambiguous primary cannot name an output")
        return self

    def primary_output(self) -> PrototypeOutput | None:
        """The output named as primary, if any."""
        return next(
            (o for o in self.outputs if o.output_json == self.primary_output_json), None
        )


class PrototypeIndex(BaseModel):
    """coach-prototype index with the explicit primary model."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = PROTOTYPE_INDEX_SCHEMA_VERSION
    provider: str | None = None
    primary_model: str | None = None
    entries: list[PrototypeIndexEntry]

    @model_validator(mode="after")
    def _primary_outputs_match_primary_model(self) -> PrototypeIndex:
        if self.schema_version == LEGACY_SCHEMA_VERSION:
            return self
        for entry in self.entries:
            primary = entry.primary_output()
            if primary is not None and primary.model != self.primary_model:
                raise ValueError(
                    f"{entry.safe_id}: primary output model {primary.model!r} "
                    f"!= primary_model {self.primary_model!r}"
                )
        return self

    def by_safe_id(self, safe_id: str) -> PrototypeIndexEntry | None:
        """Entry for ``safe_id``, if the unit was processed."""
        return next((e for e in self.entries if e.safe_id == safe_id), None)


@dataclass(frozen=True)
class UnitArtifacts:
    """Validated documents for one coaching unit."""

    entry: CoachingIndexEntry
    coach_input: CoachInput
    unit: CoachingUnitResult
    llm_view: CoachLlmView | None


@dataclass(frozen=True)
class AssessmentArtifact:
    """One saved assessment with its model label and primary flag."""

    model: str
    output: PrototypeOutput
    assessment: CoachingAssessment
    primary: bool


def coach_inputs_dir(analysis_dir: Path) -> Path:
    """``ANALYSIS_DIR/coach_inputs``."""
    return analysis_dir / COACH_INPUTS_DIRNAME


def coach_prototype_dir(analysis_dir: Path) -> Path:
    """``ANALYSIS_DIR/coach_prototype``."""
    return analysis_dir / COACH_PROTOTYPE_DIRNAME


def coaching_index_path(analysis_dir: Path) -> Path:
    """The coach-inputs index for an analysis directory."""
    return coach_inputs_dir(analysis_dir) / COACHING_INDEX_FILENAME


def write_json(path: Path, payload: Any) -> None:
    """Write pretty-printed JSON with a trailing newline."""
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_json(path: Path) -> Any:
    """Load a JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def _load_index_payload(path: Path) -> Any:
    try:
        return load_json(path)
    except json.JSONDecodeError as exc:
        raise ArtifactError(f"{path} is not valid JSON: {exc}") from exc


def _check_version(path: Path, version: int, current: int) -> None:
    if version > current:
        raise ArtifactError(
            f"{path} has schema_version {version}; this build reads up to {current}"
        )


def read_coaching_index(inputs_dir: Path) -> CoachingIndex:
    """Load ``coaching_index.json`` from a coach-inputs directory.

    Raises:
        FileNotFoundError: The index is missing.
        ArtifactError: The index is malformed or from a newer schema.
    """
    path = inputs_dir / COACHING_INDEX_FILENAME
    if not path.is_file():
        raise FileNotFoundError(f"Missing {COACHING_INDEX_FILENAME} in {inputs_dir}")
    payload = _load_index_payload(path)
    try:
        if isinstance(payload, list):
            return CoachingIndex(schema_version=LEGACY_SCHEMA_VERSION, entries=payload)
        index = CoachingIndex.model_validate(payload)
    except ValidationError as exc:
        raise ArtifactError(f"{path} does not match the index schema: {exc}") from exc
    _check_version(path, index.schema_version, COACHING_INDEX_SCHEMA_VERSION)
    return index


def read_coaching_index_or_none(inputs_dir: Path) -> CoachingIndex | None:
    """Like :func:`read_coaching_index`, but ``None`` when the file is absent."""
    if not (inputs_dir / COACHING_INDEX_FILENAME).is_file():
        return None
    return read_coaching_index(inputs_dir)


def write_coaching_index(
    inputs_dir: Path,
    entries: list[CoachingIndexEntry],
    *,
    analysis_id: str | None,
) -> Path:
    """Write the rank-ordered coach-inputs index (current schema + evidence)."""
    path = inputs_dir / COACHING_INDEX_FILENAME
    index = CoachingIndex(
        evidence_version=COACH_EVIDENCE_VERSION,
        analysis_id=analysis_id,
        entries=entries,
    )
    write_json(path, index.model_dump(mode="json"))
    return path


class StalenessReason(StrEnum):
    """Why saved coaching artifacts no longer describe the current evidence."""

    EVIDENCE_VERSION_MISSING = "evidence_version_missing"
    EVIDENCE_VERSION_OLD = "evidence_version_old"
    ANALYSIS_ID_MISMATCH = "analysis_id_mismatch"


class ArtifactStaleness(BaseModel):
    """``current`` or ``stale`` (+ reason). Stale artifacts are marked, not rebuilt."""

    status: Literal["current", "stale"]
    reason: StalenessReason | None = None

    @property
    def stale(self) -> bool:
        """True when the artifacts predate the current evidence semantics."""
        return self.status == "stale"

    def message(self) -> str:
        """One-line operator warning; empty when current."""
        if not self.stale:
            return ""
        return (
            f"coach_inputs are stale ({self.reason}); re-run "
            "`s3-coach coach-inputs` to rebuild them with current evidence."
        )


def manifest_analysis_id(manifest: Any) -> str | None:
    """``analysis.analysis_id`` from a VisionManifest or its raw JSON dict."""
    if manifest is None:
        return None
    analysis = (
        manifest.get("analysis")
        if isinstance(manifest, dict)
        else getattr(manifest, "analysis", None)
    )
    if isinstance(analysis, dict):
        value = analysis.get("analysis_id")
    else:
        value = getattr(analysis, "analysis_id", None)
    return value if isinstance(value, str) and value else None


def artifact_staleness(index: CoachingIndex, manifest: Any) -> ArtifactStaleness:
    """Compare a coaching index to the current evidence version and analysis.

    A missing ``evidence_version`` or ``analysis_id`` means stale, never current.
    When the manifest is unavailable only the evidence version is checked.
    """
    if index.evidence_version is None:
        return _stale(StalenessReason.EVIDENCE_VERSION_MISSING)
    if index.evidence_version < COACH_EVIDENCE_VERSION:
        return _stale(StalenessReason.EVIDENCE_VERSION_OLD)
    current_id = manifest_analysis_id(manifest)
    if index.analysis_id is None or (
        current_id is not None and index.analysis_id != current_id
    ):
        return _stale(StalenessReason.ANALYSIS_ID_MISMATCH)
    return ArtifactStaleness(status="current")


def _stale(reason: StalenessReason) -> ArtifactStaleness:
    return ArtifactStaleness(status="stale", reason=reason)


def coach_inputs_staleness(
    inputs_dir: Path, analysis_dir: Path
) -> ArtifactStaleness | None:
    """Staleness of ``inputs_dir`` against ``analysis_dir``; None without an index."""
    index = read_coaching_index_or_none(inputs_dir)
    if index is None:
        return None
    manifest_path = analysis_dir / VISION_MANIFEST_FILENAME
    manifest = load_json(manifest_path) if manifest_path.is_file() else None
    return artifact_staleness(index, manifest)


def read_prototype_index(prototype_dir: Path) -> PrototypeIndex | None:
    """Load the coach-prototype index, or ``None`` when it was never written."""
    path = prototype_dir / COACHING_INDEX_FILENAME
    if not path.is_file():
        return None
    payload = _load_index_payload(path)
    try:
        if isinstance(payload, list):
            return _legacy_prototype_index(prototype_dir, payload)
        index = PrototypeIndex.model_validate(payload)
    except ValidationError as exc:
        raise ArtifactError(f"{path} does not match the index schema: {exc}") from exc
    _check_version(path, index.schema_version, PROTOTYPE_INDEX_SCHEMA_VERSION)
    return index


def write_prototype_index(prototype_dir: Path, index: PrototypeIndex) -> Path:
    """Write the coach-prototype index (current schema)."""
    path = prototype_dir / COACHING_INDEX_FILENAME
    write_json(path, index.model_dump(mode="json"))
    return path


def read_document(folder: Path, name: str | None) -> dict[str, Any] | None:
    """Raw JSON object for display-only consumers; ``None`` if absent or invalid."""
    if not name:
        return None
    try:
        _bare_filename(name)
    except ValueError:
        return None
    path = folder / name
    if not path.is_file():
        return None
    try:
        payload = load_json(path)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _validated(model: type[_ModelT], path: Path) -> _ModelT:
    if not path.is_file():
        raise ArtifactError(f"Missing artifact {path}")
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise ArtifactError(f"{path} is not a valid {model.__name__}: {exc}") from exc


def load_coach_input(inputs_dir: Path, entry: CoachingIndexEntry) -> CoachInput:
    """Typed CoachInput for one index entry."""
    return _validated(CoachInput, inputs_dir / entry.coach_input_json)


def load_coaching_unit(inputs_dir: Path, entry: CoachingIndexEntry) -> CoachingUnitResult:
    """Typed CoachingUnitResult for one index entry."""
    return _validated(CoachingUnitResult, inputs_dir / entry.coaching_json)


def load_llm_view(inputs_dir: Path, entry: CoachingIndexEntry) -> CoachLlmView | None:
    """Saved CoachLlmView, or ``None`` when the index does not name one."""
    if entry.llm_view_json is None:
        return None
    return _validated(CoachLlmView, inputs_dir / entry.llm_view_json)


def load_assessment(path: Path) -> CoachingAssessment:
    """Typed CoachingAssessment from an ``*.output.json`` file."""
    return _validated(CoachingAssessment, path)


def validate_unit_relationships(
    entry: CoachingIndexEntry,
    coach_input: CoachInput,
    unit: CoachingUnitResult,
    llm_view: CoachLlmView | None,
) -> None:
    """Raise :class:`ArtifactError` when one unit's documents disagree."""
    problems: list[str] = []
    if coach_input.primary_scenario.scenario_id != entry.scenario_id:
        problems.append(
            f"coach_input scenario {coach_input.primary_scenario.scenario_id!r}"
        )
    if unit.candidate_id != entry.candidate_id:
        problems.append(f"coaching candidate {unit.candidate_id!r}")
    if unit.candidate_type != entry.candidate_type:
        problems.append(f"coaching candidate_type {unit.candidate_type!r}")
    if llm_view is not None and llm_view.unit.get("candidate_id") != entry.candidate_id:
        problems.append(f"llm_view candidate {llm_view.unit.get('candidate_id')!r}")
    if problems:
        raise ArtifactError(
            f"{entry.safe_id}: index names {entry.candidate_id!r} but "
            + "; ".join(problems)
        )


def load_unit_artifacts(inputs_dir: Path, entry: CoachingIndexEntry) -> UnitArtifacts:
    """Load and cross-check CoachInput, CoachingUnitResult, and CoachLlmView."""
    coach_input = load_coach_input(inputs_dir, entry)
    unit = load_coaching_unit(inputs_dir, entry)
    llm_view = load_llm_view(inputs_dir, entry)
    validate_unit_relationships(entry, coach_input, unit, llm_view)
    return UnitArtifacts(
        entry=entry, coach_input=coach_input, unit=unit, llm_view=llm_view
    )


def write_assessment_artifacts(
    *,
    out_dir: Path,
    safe_id: str,
    model_label: str,
    assessment: CoachingAssessment,
    raw_text: str,
    flags: dict[str, Any],
    skipped_llm: bool,
    provenance: RunProvenance | None = None,
) -> PrototypeOutput:
    """Write output/raw/flags for one model run and describe them."""
    names = output_file_names(safe_id, model_label)
    write_json(out_dir / names.output, assessment.model_dump(mode="json"))
    (out_dir / names.raw).write_text(raw_text, encoding="utf-8")
    write_json(out_dir / names.flags, flags)
    return PrototypeOutput(
        model=model_label,
        output_json=names.output,
        raw_txt=names.raw,
        flags_json=names.flags,
        skipped_llm=skipped_llm,
        provenance=provenance,
    )


@dataclass(frozen=True)
class _UnitOutputs:
    outputs: list[PrototypeOutput]
    primary_output_json: str | None
    primary_ambiguous: bool

    def primary_output(self) -> PrototypeOutput | None:
        return next(
            (o for o in self.outputs if o.output_json == self.primary_output_json), None
        )


def unit_assessments(prototype_dir: Path, safe_id: str) -> list[AssessmentArtifact]:
    """Every saved assessment for one unit, primary first."""
    entry = _unit_outputs(prototype_dir, safe_id)
    if entry is None:
        return []
    found: list[AssessmentArtifact] = []
    for output in entry.outputs:
        path = prototype_dir / output.output_json
        if not path.is_file():
            continue
        found.append(
            AssessmentArtifact(
                model=output.model,
                output=output,
                assessment=load_assessment(path),
                primary=output.output_json == entry.primary_output_json,
            )
        )
    return sorted(found, key=lambda item: not item.primary)


def primary_assessment(prototype_dir: Path, safe_id: str) -> CoachingAssessment | None:
    """The explicitly selected assessment for one unit, or ``None``.

    Raises:
        ArtifactError: A legacy unit has several outputs and no recorded primary.
    """
    entry = _unit_outputs(prototype_dir, safe_id)
    if entry is None:
        return None
    if entry.primary_ambiguous:
        raise ArtifactError(
            f"{prototype_dir}: {safe_id} has several model outputs and no recorded "
            "primary model (legacy artifacts). Re-run `s3-coach coach-prototype`."
        )
    output = entry.primary_output()
    if output is None:
        return None
    path = prototype_dir / output.output_json
    return load_assessment(path) if path.is_file() else None


def prototype_outputs(prototype_dir: Path) -> list[PrototypeOutput]:
    """Every model output recorded in a coach-prototype directory.

    Raises:
        ArtifactError: The directory has no coach-prototype index.
    """
    index = read_prototype_index(prototype_dir)
    if index is None:
        raise ArtifactError(
            f"No {COACHING_INDEX_FILENAME} in {prototype_dir}; "
            "re-run `s3-coach coach-prototype`."
        )
    return [output for entry in index.entries for output in entry.outputs]


def _unit_outputs(prototype_dir: Path, safe_id: str) -> _UnitOutputs | None:
    if not safe_id or not prototype_dir.is_dir():
        return None
    index = read_prototype_index(prototype_dir)
    if index is not None:
        entry = index.by_safe_id(safe_id)
        if entry is None:
            return None
        return _UnitOutputs(
            entry.outputs, entry.primary_output_json, entry.primary_ambiguous
        )
    outputs = _legacy_outputs(prototype_dir, safe_id)
    if not outputs:
        return None
    return _UnitOutputs(outputs, *_legacy_primary(outputs))


def _legacy_outputs(prototype_dir: Path, safe_id: str) -> list[PrototypeOutput]:
    prefix = f"{safe_id}."
    outputs: list[PrototypeOutput] = []
    for path in sorted(prototype_dir.glob(f"{safe_id}.*{OUTPUT_SUFFIX}")):
        tag = path.name[len(prefix) : -len(OUTPUT_SUFFIX)]
        if not path.name.startswith(prefix) or not tag:
            continue
        stem = f"{safe_id}.{tag}"
        outputs.append(
            PrototypeOutput(
                model=tag,
                output_json=path.name,
                raw_txt=f"{stem}{RAW_SUFFIX}",
                flags_json=f"{stem}{FLAGS_SUFFIX}",
            )
        )
    return outputs


def _legacy_primary(outputs: list[PrototypeOutput]) -> tuple[str | None, bool]:
    if len(outputs) == 1:
        return outputs[0].output_json, False
    return None, len(outputs) > 1


def _legacy_prototype_index(prototype_dir: Path, rows: list[Any]) -> PrototypeIndex:
    entries: list[PrototypeIndexEntry] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ArtifactError(f"{prototype_dir}: legacy index row is not an object")
        safe_id = str(row.get("coach_input_json", "")).removesuffix(_COACH_INPUT_SUFFIX)
        outputs = _legacy_outputs(prototype_dir, safe_id)
        primary, ambiguous = _legacy_primary(outputs)
        entries.append(
            PrototypeIndexEntry.model_validate(
                {
                    **row,
                    "safe_id": safe_id,
                    "outputs": outputs,
                    "primary_output_json": primary,
                    "primary_ambiguous": ambiguous,
                }
            )
        )
    return PrototypeIndex(schema_version=LEGACY_SCHEMA_VERSION, entries=entries)
