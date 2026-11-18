"""Review-code canary capture and ``correct_code`` ordering against coach_test."""

from __future__ import annotations

import json
from pathlib import Path

from public_site.report_canary import (
    CANARY_RULE,
    UNCHECKED_CANARY,
    CanaryStore,
    CanaryUnavailable,
)
from public_site.report_content import ContentFinding
from public_site.report_known import recordings_lookup, submission_known_values
from test_public_recordings import _create, _login, _paid, site  # noqa: F401

CODE = "ABCD-1234-EFGH-5678"
CORRECTED = "1111-2222-3333-4444"


def _canary(site) -> CanaryStore:  # noqa: F811
    return site.app.state.service.recordings.canary


def _hand_off(site, submission_id: str) -> None:  # noqa: F811
    database = site.app.state.service.recordings.database
    with database._conn() as conn:
        conn.execute(
            "UPDATE review_code_acquisitions SET state='uploading' "
            "WHERE submission_id=%s",
            (submission_id,),
        )
    assert database.confirm_recording_handoff(submission_id, "handoff-canary")


def _known(site, submission_id: str):  # noqa: F811
    svc = site.app.state.service
    submission = svc.store.get(submission_id)
    return submission_known_values(
        svc.settings, svc.store, submission, recordings_lookup(svc), _canary(site)
    )


def _caught(known, value: str) -> str | None:
    try:
        known.check(json.dumps({"note": value}).encode())
    except ContentFinding as exc:
        return exc.rule
    return None


def test_canary_catches_the_code_after_handoff_nulls_it(site) -> None:  # noqa: F811
    created = _paid(site)
    submission_id = created["submission_id"]
    assert (_canary(site).root / f"{submission_id}.json").is_file()
    _hand_off(site, submission_id)
    row = site.app.state.service.recordings.database.get(submission_id)
    assert row["review_code"] is None
    result = _known(site, submission_id)
    assert result.unchecked == []
    assert _caught(result.known, "abcd 1234 efgh 5678") == CANARY_RULE


def test_capture_failure_refuses_the_submission(site, monkeypatch) -> None:  # noqa: F811
    def fail(_self, _submission_id, _code):
        raise CanaryUnavailable

    monkeypatch.setattr(CanaryStore, "capture", fail)
    database = site.app.state.service.recordings.database
    before = len(database.snapshots())
    response = site.post(
        "/api/review-code-submissions",
        json={
            "turnstile_token": "ok",
            "review_code": CODE,
            "email": "player@example.com",
        },
    )
    assert response.status_code == 503
    assert CODE not in response.text
    assert len(database.snapshots()) == before


def _failed_job(site) -> tuple[str, str, dict]:  # noqa: F811
    created = _paid(site)
    _login(site, admin=True)
    endpoint = f"/api/admin/recordings/{created['submission_id']}"
    job = site.get(endpoint).json()["job"]
    failed = site.post(
        endpoint + "/actions",
        json={
            "action": "recording_failed",
            "expected_version": job["version"],
            "note": "Replay not found",
            "customer_message": "Please confirm your code.",
        },
    )
    assert failed.status_code == 200, failed.text
    return created["submission_id"], endpoint, failed.json()["job"]


def _correct(site, endpoint: str, job: dict, version: int | None = None):  # noqa: F811
    return site.post(
        endpoint + "/actions",
        json={
            "action": "correct_code",
            "expected_version": job["version"] if version is None else version,
            "note": "Customer confirmed the code",
            "review_code": CORRECTED,
        },
    )


def test_correct_code_replaces_the_canary(site) -> None:  # noqa: F811
    submission_id, endpoint, job = _failed_job(site)
    assert _correct(site, endpoint, job).status_code == 200
    assert not (_canary(site).root / f"{submission_id}.pending.json").exists()
    known = _known(site, submission_id).known
    assert _caught(known, CORRECTED) == CANARY_RULE


def test_correct_code_promotion_failure_keeps_publication_blocked(
    site,  # noqa: F811
    monkeypatch,
) -> None:
    submission_id, endpoint, job = _failed_job(site)

    def fail(_self, _submission_id):
        raise CanaryUnavailable

    with monkeypatch.context() as patch:
        patch.setattr(CanaryStore, "commit_correction", fail)
        response = _correct(site, endpoint, job)
    assert response.status_code == 500
    assert "blocked" in response.json()["detail"]
    assert CORRECTED not in response.text
    database = site.app.state.service.recordings.database
    assert database.get(submission_id)["review_code"] == CORRECTED
    canary = _canary(site)
    assert (canary.root / f"{submission_id}.pending.json").exists()
    assert _known(site, submission_id).unchecked == [UNCHECKED_CANARY]
    assert canary.pending_states(database.get) == {submission_id: "promotable"}
    site.app.state.service.recordings.repair_canaries()
    assert _known(site, submission_id).unchecked == []
    assert _caught(_known(site, submission_id).known, CORRECTED) == CANARY_RULE


def test_failed_correct_code_update_removes_the_pending_file(site) -> None:  # noqa: F811
    submission_id, endpoint, job = _failed_job(site)
    stale = _correct(site, endpoint, job, version=job["version"] - 1)
    assert stale.status_code == 409
    canary = _canary(site)
    assert not (canary.root / f"{submission_id}.pending.json").exists()
    assert _caught(_known(site, submission_id).known, CODE) == CANARY_RULE


def test_correct_code_staging_failure_changes_nothing(site, monkeypatch) -> None:  # noqa: F811
    submission_id, endpoint, job = _failed_job(site)

    def fail(_self, _submission_id, _code):
        raise CanaryUnavailable

    monkeypatch.setattr(CanaryStore, "stage_correction", fail)
    assert _correct(site, endpoint, job).status_code == 503
    database = site.app.state.service.recordings.database
    row = database.get(submission_id)
    assert row["review_code"] == CODE and row["state"] == "recording_failed"


def test_pending_after_handoff_stays_blocked(site, monkeypatch) -> None:  # noqa: F811
    submission_id, endpoint, job = _failed_job(site)

    def fail(_self, _submission_id):
        raise CanaryUnavailable

    with monkeypatch.context() as patch:
        patch.setattr(CanaryStore, "commit_correction", fail)
        assert _correct(site, endpoint, job).status_code == 500
    _hand_off(site, submission_id)
    canary = _canary(site)
    database = site.app.state.service.recordings.database
    assert canary.pending_states(database.get) == {submission_id: "no_code"}
    site.app.state.service.recordings.repair_canaries()
    assert (canary.root / f"{submission_id}.pending.json").exists()
    assert _known(site, submission_id).unchecked == [UNCHECKED_CANARY]


def test_capture_happens_before_the_row_exists(site, monkeypatch) -> None:  # noqa: F811
    seen: list[bool] = []
    database = site.app.state.service.recordings.database
    original = CanaryStore.capture

    def capture(self, submission_id, code):
        seen.append(database.get(submission_id) is None)
        original(self, submission_id, code)

    monkeypatch.setattr(CanaryStore, "capture", capture)
    created = _create(site)
    assert seen == [True]
    assert Path(_canary(site).root / f"{created['submission_id']}.json").is_file()


def test_canary_key_exists_before_the_first_submission(site) -> None:  # noqa: F811
    canary = _canary(site)
    key = canary.load_key()
    created = _create(site)
    submission = site.app.state.service.store.get(created["submission_id"])
    assert key.created_at <= submission.created_at
