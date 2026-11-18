"""The known values checked before publishing, shared by the publisher and ``scan``.

Both paths call ``submission_known_values`` so the operator scan applies exactly
the canaries publication applies. A value group that cannot be retrieved is
reported by name in ``unchecked``: the publisher fails closed on it, the scan
reports it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from public_site.models import Submission
from public_site.report_bundle import known_values
from public_site.report_canary import CANARY_RULE, UNCHECKED_CANARY, CanaryStore
from public_site.report_content import KnownValues
from public_site.settings import PublicSettings
from public_site.store import PublicStore

AcquisitionLookup = Callable[[str], dict | None]
UNCHECKED_SUBMISSION = "submission_values"
UNCHECKED_ACQUISITION = "acquisition_values"


@dataclass
class KnownResult:
    """Known values plus the names of the value groups that could not be retrieved."""

    known: KnownValues
    unchecked: list[str] = field(default_factory=list)


def submission_known_values(
    settings: PublicSettings,
    store: PublicStore,
    submission: Submission | None,
    lookup: AcquisitionLookup | None,
    canary: CanaryStore | None,
) -> KnownResult:
    """Personal values, acquisition values, the review-code canary and configured
    secrets for one submission.

    - Without a submission projection, its personal values are ``unchecked``.
    - A lookup error marks the acquisition ``unchecked`` for any submission.
    - A review-code submission must have an acquisition row: no lookup, a lookup
      error or no row (including a database without the acquisition table) all
      mark its acquisition ``unchecked``.
    - An upload without an acquisition row is fully checked.
    - A review-code submission's canary (``CanaryStore.matcher``) is required
      from the canary epoch on; without a canary store, or when the canary
      cannot be used, ``review_code_canary`` is ``unchecked``.
    """
    if submission is None:
        return KnownResult(known_values(settings, None, None), [UNCHECKED_SUBMISSION])
    acquisition, available = _acquisition(submission, lookup)
    known = known_values(
        settings, submission, store.get_video(submission.id), None, acquisition
    )
    unchecked = [] if available else [UNCHECKED_ACQUISITION]
    if not _add_canary(known, submission, canary):
        unchecked.append(UNCHECKED_CANARY)
    return KnownResult(known, unchecked)


def _add_canary(
    known: KnownValues, submission: Submission, canary: CanaryStore | None
) -> bool:
    """Register the submission's canary matcher; False when the group is unchecked."""
    if submission.input_type != "review_code":
        return True
    if canary is None:
        return False
    matcher, checked = canary.matcher(submission)
    if matcher is not None:
        known.add_code(CANARY_RULE, matcher)
    return checked


def _acquisition(
    submission: Submission, lookup: AcquisitionLookup | None
) -> tuple[dict | None, bool]:
    """The acquisition row and whether its values are known."""
    review_code = submission.input_type == "review_code"
    if lookup is None:
        return None, not review_code
    try:
        acquisition = lookup(submission.id)
    except Exception:  # noqa: BLE001 — any lookup failure: values unknown
        return None, False
    if acquisition is None:
        return None, not review_code
    return acquisition, True


def recordings_lookup(service) -> AcquisitionLookup | None:
    """The app's acquisition lookup (``service.recordings.database.get``), if any."""
    recordings = getattr(service, "recordings", None)
    if recordings is None:
        return None
    return recordings.database.get


def database_lookup(settings: PublicSettings) -> AcquisitionLookup | None:
    """A read-only acquisition lookup from ``database_url``, or None without one.

    Uses the same ``RecordingStore.get`` query as the app. A database without the
    acquisition table holds no acquisitions, so that reads as "no row": an
    upload stays fully checked and a review-code submission is ``unchecked``.
    Every other error propagates (and becomes ``unchecked``).
    """
    if not settings.database_url:
        return None
    from psycopg import errors

    from public_site.payments import PaymentStore
    from public_site.recording_store import RecordingStore

    records = RecordingStore(PaymentStore(settings.database_url))

    def lookup(submission_id: str) -> dict | None:
        try:
            return records.get(submission_id)
        except errors.UndefinedTable:
            return None

    return lookup
