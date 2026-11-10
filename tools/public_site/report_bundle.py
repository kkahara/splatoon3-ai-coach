"""Published report bundles: the allowlist, ``bundle.json`` v1, and preparation.

Only what serving reads is published. Each local file is read once: those
bytes give ``local_sha256``; transforms run on the same bytes and give the
published bytes and ``published_sha256``; the content scanner runs on the
published bytes. Transformed bytes go to owner-only staging; untransformed
files upload straight from the analysis folder, which is never modified.
"""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from public_site.models import ReviewCodeInput, Submission, VideoInput
from public_site.report_content import (
    CONTENT_POLICY_VERSION,
    MIN_FILENAME_STEM,
    ContentFinding,
    KnownValues,
    scan_jpeg,
    scan_json,
    scan_mp4,
)
from public_site.report_staging import StagingRun, StagingUnsafe
from public_site.report_transform import (
    MANIFEST_PATH,
    ReportIdentity,
    TransformError,
    new_pseudonym,
    read_identity,
    transform_json,
    verify_published,
)
from public_site.settings import PublicSettings
from public_site.storage import file_sha256, valid_key

BUNDLE_FORMAT = "s3coach.report_bundle.v1"
BUNDLE_NAME = "bundle.json"
INPUTS_INDEX = "coach_inputs/coaching_index.json"
_ROOT_FILES = (MANIFEST_PATH, "match_identity.json")
_DIR_SUFFIXES = {
    "coach_inputs": (
        "coaching_index.json",
        ".coaching.json",
        ".coach_input.json",
        ".llm_view.json",
    ),
    "coach_prototype": (
        "coaching_index.json",
        ".output.json",
        ".flags.json",
        ".revalidation.json",
        ".llm_view.json",
    ),
    "public_clips": (".mp4",),
    "public_frames": (".jpg",),
}
_CONTENT_TYPES = {".json": "application/json", ".mp4": "video/mp4", ".jpg": "image/jpeg"}
ErrorKind = Literal[
    "local_missing",
    "invalid_key",
    "transform_failed",
    "content_policy",
    "staging_unsafe",
    "local_changed",
]


class BundleError(Exception):
    """Preparation failed. ``kind`` is closed vocabulary; ``rule`` is a fixed name."""

    def __init__(self, kind: ErrorKind, rule: str | None = None) -> None:
        super().__init__(kind if rule is None else f"{kind}:{rule}")
        self.kind = kind
        self.rule = rule


class BundleFile(BaseModel):
    """One published object, with local and published hashes."""

    model_config = ConfigDict(extra="forbid")

    path: str
    local_size: int
    local_sha256: str
    published_size: int
    published_sha256: str
    transforms: list[str]


class BundleProducer(BaseModel):
    """Pipeline provenance. No analysis or video fingerprint."""

    model_config = ConfigDict(extra="forbid")

    pipeline_version: str | None = None
    git_sha: str | None = None
    evidence_version: int | None = None


class ReportBundle(BaseModel):
    """``bundle.json``: written last; its presence commits the run."""

    model_config = ConfigDict(extra="forbid")

    format: Literal["s3coach.report_bundle.v1"] = BUNDLE_FORMAT
    submission_id: str
    run_id: str
    created_at: str
    outcome: str | None
    producer: BundleProducer
    missing_media: list[str]
    content_policy_version: int = CONTENT_POLICY_VERSION
    total_bytes: int
    files: list[BundleFile]


@dataclass
class PreparedFile:
    """One file ready to upload from ``source``."""

    path: str
    source: Path
    local_path: Path
    content_type: str
    local_size: int
    local_sha256: str
    published_size: int
    published_sha256: str
    transforms: list[str]
    staged: bool = False


@dataclass
class PreparedBundle:
    """Everything one run uploads. ``bundle_source`` is None in scan-only mode."""

    bundle: ReportBundle
    files: list[PreparedFile]
    bundle_bytes: bytes
    bundle_sha256: str
    bundle_source: Path | None
    published_json: dict[str, bytes] = field(default_factory=dict, repr=False)


def select_files(analysis: Path, outcome: str | None) -> list[str]:
    """Allowlisted relative paths that exist, sorted. Raises BundleError."""
    found: list[str] = []
    for name in _ROOT_FILES:
        if _regular(analysis / name):
            found.append(name)
    for folder, suffixes in _DIR_SUFFIXES.items():
        directory = analysis / folder
        if not _real_dir(directory):
            continue
        for entry in sorted(directory.iterdir()):
            name = entry.name
            if name.startswith(".") or not name.endswith(suffixes) or not _regular(entry):
                continue
            found.append(f"{folder}/{name}")
    if MANIFEST_PATH not in found:
        raise BundleError("local_missing")
    if outcome == "coached" and INPUTS_INDEX not in found:
        raise BundleError("local_missing")
    return sorted(found)


def _regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _real_dir(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


def run_key(submission_id: str, run_id: str, path: str) -> str:
    """Object key of one published file. Raises BundleError(invalid_key)."""
    key = f"reports/{submission_id}/{run_id}/{path}"
    if not valid_key(key):
        raise BundleError("invalid_key")
    return key


def known_values(
    settings: PublicSettings,
    submission: Submission | None,
    video: VideoInput | None,
    review: ReviewCodeInput | None = None,
    acquisition: dict | None = None,
) -> KnownValues:
    """Values that must never be published: personal fields and configured secrets."""
    known = KnownValues()
    if submission is not None:
        known.add_personal("known_value:email", submission.email)
        known.add_name("known_value:display_name", submission.display_name)
        known.add_personal("known_value:user_id", _text(submission.user_id))
        known.add_secret("known_value:access_token_hash", submission.access_token_hash)
    if video is not None:
        known.add_personal("known_value:original_filename", video.original_filename)
        known.add(
            "known_value:original_filename",
            Path(video.original_filename).stem,
            minimum=MIN_FILENAME_STEM,
        )
    if review is not None:
        known.add_personal("known_value:review_code", review.review_code)
    _add_acquisition(known, acquisition or {})
    for name in (
        "r2_access_key_id",
        "r2_secret_access_key",
        "smtp_password",
        "stripe_secret_key",
        "stripe_webhook_secret",
        "voucher_hmac_secret",
        "turnstile_secret",
        "database_url",
    ):
        known.add_secret(f"secret:{name}", getattr(settings, name, None))
    return known


ACQUISITION_FIELDS = (
    "review_code",
    "target_player_name",
    "confirmed_player_name",
    "requested_email",
    "customer_message",
    "handoff_reference",
)
SNAPSHOT_FIELDS = ("email", "display_name", "user_id")


_NAME_FIELDS = frozenset({"target_player_name", "confirmed_player_name", "display_name"})


def _text(value: object) -> str | None:
    """A stored value as text: strings as-is, numbers (e.g. a numeric
    ``user_id``) via ``str``, anything else ignored.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return None


def _add_value(known: KnownValues, name: str, value: object) -> None:
    rule = f"known_value:{name}"
    if name in _NAME_FIELDS:
        known.add_name(rule, value if isinstance(value, str) else None)
    else:
        known.add_personal(rule, _text(value))


def _add_acquisition(known: KnownValues, acquisition: dict) -> None:
    """Review-code acquisition values (P1-A ``review_code_acquisitions`` row)."""
    for name in ACQUISITION_FIELDS:
        _add_value(known, name, acquisition.get(name))
    snapshot = acquisition.get("submission_snapshot")
    if isinstance(snapshot, dict):
        for name in SNAPSHOT_FIELDS:
            _add_value(known, name, snapshot.get(name))
        token_hash = snapshot.get("access_token_hash")
        if isinstance(token_hash, str):
            known.add_secret("known_value:access_token_hash", token_hash)


def prepare_bundle(
    analysis: Path,
    *,
    submission_id: str,
    run_id: str,
    outcome: str | None,
    created_at: str,
    known: KnownValues,
    staging: StagingRun | None,
    pseudonym: str | None = None,
) -> PreparedBundle:
    """Read, transform, scan and stage one run. Nothing is uploaded here.

    Raises:
        BundleError: local_missing, invalid_key, transform_failed,
            content_policy, staging_unsafe or local_changed.
    """
    paths = select_files(analysis, outcome)
    for path in paths:
        run_key(submission_id, run_id, path)
    try:
        identity = read_identity((analysis / MANIFEST_PATH).read_bytes())
    except TransformError as exc:
        raise BundleError("transform_failed", str(exc)) from None
    run_known = known.copy()
    run_known.add_secret("original_fingerprint", identity.video_identity)
    run_known.add_secret("original_fingerprint", identity.analysis_id)
    pseudonym = pseudonym or new_pseudonym(identity)
    files, published_json = _prepare_files(
        analysis, paths, identity, pseudonym, run_known, staging
    )
    try:
        verify_published(published_json, identity, pseudonym)
    except TransformError as exc:
        raise BundleError("transform_failed", str(exc)) from None
    bundle = ReportBundle(
        submission_id=submission_id,
        run_id=run_id,
        created_at=created_at,
        outcome=outcome,
        producer=_producer(published_json),
        missing_media=_missing_media(analysis),
        total_bytes=sum(item.published_size for item in files),
        files=[_bundle_file(item) for item in files],
    )
    return _finish(bundle, files, run_known, staging, published_json)


def _prepare_files(
    analysis: Path,
    paths: list[str],
    identity: ReportIdentity,
    pseudonym: str,
    known: KnownValues,
    staging: StagingRun | None,
) -> tuple[list[PreparedFile], dict[str, bytes]]:
    files: list[PreparedFile] = []
    published_json: dict[str, bytes] = {}
    for path in paths:
        prepared, published = prepare_file(
            analysis, path, identity, pseudonym, known, staging
        )
        files.append(prepared)
        if published is not None:
            published_json[path] = published
    return files, published_json


def prepare_file(
    analysis: Path,
    path: str,
    identity: ReportIdentity,
    pseudonym: str,
    known: KnownValues,
    staging: StagingRun | None,
) -> tuple[PreparedFile, bytes | None]:
    """Read one file once, transform, scan and (if transformed) stage it.

    Returns the prepared file and, for JSON, the published bytes.

    Raises:
        BundleError: As ``prepare_bundle``.
    """
    source = analysis / path
    try:
        data = source.read_bytes()
    except OSError:
        raise BundleError("local_missing") from None
    local_sha = hashlib.sha256(data).hexdigest()
    suffix = source.suffix
    published, transforms = data, []
    try:
        if suffix == ".json":
            published, transforms = transform_json(path, data, identity, pseudonym)
            scan_json(published, known)
        elif suffix == ".jpg":
            scan_jpeg(published, known)
        else:
            scan_mp4(source, published, known)
            if file_sha256(source) != local_sha:
                raise BundleError("local_changed")
    except TransformError as exc:
        raise BundleError("transform_failed", str(exc)) from None
    except ContentFinding as exc:
        raise BundleError("content_policy", exc.rule) from None
    prepared = PreparedFile(
        path=path,
        source=source,
        local_path=source,
        content_type=_CONTENT_TYPES[suffix],
        local_size=len(data),
        local_sha256=local_sha,
        published_size=len(published),
        published_sha256=hashlib.sha256(published).hexdigest(),
        transforms=transforms,
    )
    if transforms and staging is not None:
        prepared.source = _stage(staging, published, suffix)
        prepared.staged = True
    return prepared, published if suffix == ".json" else None


def _stage(staging: StagingRun, data: bytes, suffix: str) -> Path:
    try:
        return staging.write(data, suffix.lstrip("."))
    except (StagingUnsafe, OSError):
        raise BundleError("staging_unsafe") from None


def _finish(
    bundle: ReportBundle,
    files: list[PreparedFile],
    known: KnownValues,
    staging: StagingRun | None,
    published_json: dict[str, bytes],
) -> PreparedBundle:
    body = (
        json.dumps(bundle.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    ).encode()
    try:
        scan_json(body, known)
    except ContentFinding as exc:
        raise BundleError("content_policy", exc.rule) from None
    source = _stage(staging, body, ".json") if staging is not None else None
    return PreparedBundle(
        bundle=bundle,
        files=files,
        bundle_bytes=body,
        bundle_sha256=hashlib.sha256(body).hexdigest(),
        bundle_source=source,
        published_json=published_json,
    )


def _bundle_file(item: PreparedFile) -> BundleFile:
    return BundleFile(
        path=item.path,
        local_size=item.local_size,
        local_sha256=item.local_sha256,
        published_size=item.published_size,
        published_sha256=item.published_sha256,
        transforms=list(item.transforms),
    )


def _producer(published_json: dict[str, bytes]) -> BundleProducer:
    manifest = json.loads(published_json[MANIFEST_PATH])
    analysis = manifest.get("analysis") or {}
    index = json.loads(published_json.get(INPUTS_INDEX, b"{}"))
    evidence = index.get("evidence_version") if isinstance(index, dict) else None
    return BundleProducer(
        pipeline_version=analysis.get("pipeline_version"),
        git_sha=analysis.get("git_sha"),
        evidence_version=evidence if isinstance(evidence, int) else None,
    )


def _missing_media(analysis: Path) -> list[str]:
    from public_site.clips import clip_path, has_clip
    from public_site.frames import frame_path, has_frame, ranked_entries

    missing: list[str] = []
    for entry in ranked_entries(analysis):
        clip = clip_path(analysis, entry.safe_id)
        frame = frame_path(analysis, entry.safe_id)
        if clip is not None and not has_clip(analysis, entry.safe_id):
            missing.append(clip.relative_to(analysis).as_posix())
        if frame is not None and not has_frame(analysis, entry.safe_id):
            missing.append(frame.relative_to(analysis).as_posix())
    return sorted(missing)


def key_paths(payload: object, prefix: str = "") -> set[str]:
    """Every key path in a JSON value (``a.b[].c``). Names only, never values."""
    found: set[str] = set()
    if isinstance(payload, dict):
        for name, value in payload.items():
            path = f"{prefix}.{name}"
            found.add(path)
            found |= key_paths(value, path)
    elif isinstance(payload, list):
        for value in payload:
            found |= key_paths(value, f"{prefix}[]")
    return found
