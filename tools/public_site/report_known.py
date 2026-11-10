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
) -> KnownResult:
    """Personal values, acquisition values and configured secrets for one submission.

    - Without a submission projection, its personal values are ``unchecked``.
    - With an acquisition lookup, every submission's acquisition row is read (a
      plain upload has none). A lookup error marks the acquisition ``unchecked``.
    - Without a lookup, a review-code submission's acquisition is ``unchecked``;
      an upload has no acquisition to check.
    """
    unchecked: list[str] = []
    if submission is None:
        return KnownResult(known_values(settings, None, None), [UNCHECKED_SUBMISSION])
    acquisition = None
    if lookup is not None:
        try:
            acquisition = lookup(submission.id)
        except Exception:  # noqa: BLE001 — any lookup failure: values unknown
            unchecked.append(UNCHECKED_ACQUISITION)
    elif submission.input_type == "review_code":
        unchecked.append(UNCHECKED_ACQUISITION)
    known = known_values(
        settings, submission, store.get_video(submission.id), None, acquisition
    )
    return KnownResult(known, unchecked)


def recordings_lookup(service) -> AcquisitionLookup | None:
    """The app's acquisition lookup (``service.recordings.database.get``), if any."""
    recordings = getattr(service, "recordings", None)
    if recordings is None:
        return None
    return recordings.database.get


def database_lookup(settings: PublicSettings) -> AcquisitionLookup | None:
    """A read-only acquisition lookup from ``database_url``, or None without one.

    Uses the same ``RecordingStore.get`` query as the app. A database without the
    acquisition table holds no acquisitions, so that reads as "no row"; every
    other error propagates (and becomes ``unchecked``).
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
