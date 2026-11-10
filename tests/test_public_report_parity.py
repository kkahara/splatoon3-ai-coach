"""The operator scan applies exactly the publisher's known values (R1 parity)."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from fake_s3_helpers import fake_s3
from public_site.models import Submission
from public_site.report_bundle import BundleError, prepare_bundle
from public_site.report_known import (
    UNCHECKED_ACQUISITION,
    UNCHECKED_SUBMISSION,
    KnownResult,
    database_lookup,
    submission_known_values,
)
from public_site.report_ops import scan
from public_site.store import PublicStore
from report_bundle_fixture import add_media, write_report_fixture
from report_test_helpers import (
    complete_submission,
    methods,
    report_client,
    report_settings,
)
from test_public_recordings import _create, site  # noqa: F401 — P1-A site fixture

SID = "c" * 32
PLANT_FILE = "coach_prototype/death_episode_0.5.model.output.json"
ACQUISITION = {
    "review_code": "ABCD1234EFGH5678",
    "target_player_name": "イカすプレイヤー",
    "confirmed_player_name": "タコ・ボーイ",
    "requested_email": "acq.person@example.net",
    "customer_message": "Please review my Tower Control games",
    "handoff_reference": "HANDOFF-7781-QX",
    "submission_snapshot": {
        "email": "snapshot.person@example.net",
        "display_name": "SnapshotName",
        "user_id": "acct-9f1e2d",
        "access_token_hash": "9" * 64,
    },
}
FIELDS = [
    ("review_code", "ABCD1234EFGH5678"),
    ("target_player_name", "イカすプレイヤー"),
    ("confirmed_player_name", "タコ・ボーイ"),
    ("requested_email", "acq.person@example.net"),
    ("customer_message", "Please review my Tower Control games"),
    ("handoff_reference", "HANDOFF-7781-QX"),
    ("email", "snapshot.person@example.net"),
    ("display_name", "SnapshotName"),
    ("user_id", "acct-9f1e2d"),
    ("access_token_hash", "9" * 64),
]


def _setup(tmp_path: Path, *, input_type: str = "review_code", name: str = SID):
    settings = report_settings(tmp_path, None)
    store = PublicStore(settings.root)
    store.save_submission(
        Submission(
            id=SID,
            access_token_hash="a" * 64,
            email="submitter@example.org",
            input_type=input_type,
            created_at="2026-11-09T00:00:00Z",
            updated_at="2026-11-09T00:00:00Z",
            expires_at="2026-12-09T00:00:00Z",
            status="complete",
        )
    )
    analysis = settings.root / "analysis" / name
    write_report_fixture(analysis)
    add_media(analysis)
    return settings, store, analysis


def _plant(analysis: Path, value: str) -> None:
    path = analysis / PLANT_FILE
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["assessment"] = f"Nice push, {value}."
    path.write_text(json.dumps(payload), encoding="utf-8")


def _publisher_rule(settings, store, analysis: Path, lookup) -> str | None:
    result = submission_known_values(settings, store, store.get(SID), lookup)
    assert result.unchecked == []
    try:
        prepare_bundle(
            analysis,
            submission_id=SID,
            run_id="20261109T120000Z-0000000d",
            outcome="coached",
            created_at="2026-11-09T12:00:00Z",
            known=result.known,
            staging=None,
        )
    except BundleError as exc:
        return exc.rule
    return None


@pytest.mark.parametrize(("field", "value"), FIELDS, ids=[f for f, _ in FIELDS])
def test_planted_acquisition_value_is_caught_by_scan_and_publisher_alike(
    tmp_path: Path, field: str, value: str
) -> None:
    settings, store, analysis = _setup(tmp_path)
    _plant(analysis, value)
    lookup = {SID: ACQUISITION}.get
    report = scan(settings, acquisitions=lookup)
    rule = f"known_value:{field}"
    assert report.findings == {f"content_policy:{rule}": 1}
    assert (report.analyses_clean, report.analyses_unchecked) == (0, 0)
    assert _publisher_rule(settings, store, analysis, lookup) == rule


def test_target_player_name_cannot_escape_through_json_escapes_case_or_spacing(
    tmp_path: Path,
) -> None:
    settings, _store, analysis = _setup(tmp_path)
    lookup = {SID: {"target_player_name": "Ika Sensei", "review_code": "X" * 16}}.get
    for planted in ("IKA SENSEI", "ika-sensei", "IkaSensei"):
        _plant(analysis, planted)
        assert scan(settings, acquisitions=lookup).findings == {
            "content_policy:known_value:target_player_name": 1
        }, planted
    escaped = json.dumps("イカすプレイヤー")
    assert "\\u" in escaped
    _plant(analysis, "イカすプレイヤー")
    jp = {SID: {"target_player_name": "イカすプレイヤー"}}.get
    assert scan(settings, acquisitions=jp).findings == {
        "content_policy:known_value:target_player_name": 1
    }


def test_clean_review_code_analysis_with_its_acquisition_is_clean(tmp_path: Path) -> None:
    settings, _store, _analysis = _setup(tmp_path)
    report = scan(settings, acquisitions={SID: ACQUISITION}.get)
    assert report.findings == {}
    assert (report.analyses, report.analyses_clean, report.analyses_unchecked) == (
        1,
        1,
        0,
    )
    assert report.unchecked == {}


def test_review_code_analysis_without_a_lookup_is_reported_unchecked(
    tmp_path: Path,
) -> None:
    settings, _store, analysis = _setup(tmp_path)
    _plant(analysis, "イカすプレイヤー")
    report = scan(settings)
    assert report.findings == {}
    assert report.analyses_clean == 0
    assert report.analyses_unchecked == 1
    assert report.unchecked == {UNCHECKED_ACQUISITION: 1}


def test_failing_lookup_is_reported_unchecked_not_clean(tmp_path: Path) -> None:
    settings, _store, _analysis = _setup(tmp_path, input_type="video")

    def broken(_submission_id: str) -> dict | None:
        raise ConnectionError("database unavailable")

    report = scan(settings, acquisitions=broken)
    assert (report.analyses_clean, report.analyses_unchecked) == (0, 1)
    assert report.unchecked == {UNCHECKED_ACQUISITION: 1}


def test_upload_without_a_lookup_is_fully_checked(tmp_path: Path) -> None:
    settings, _store, _analysis = _setup(tmp_path, input_type="video")
    report = scan(settings)
    assert (report.analyses_clean, report.analyses_unchecked) == (1, 0)


def test_analysis_without_a_submission_projection_is_unchecked(tmp_path: Path) -> None:
    settings, _store, _analysis = _setup(tmp_path, name="d" * 32)
    report = scan(settings, acquisitions={}.get)
    assert report.unchecked == {UNCHECKED_SUBMISSION: 1}
    assert (report.analyses_clean, report.analyses_unchecked) == (0, 1)


def test_publisher_fails_closed_when_acquisition_values_are_unknown(
    tmp_path: Path,
) -> None:
    with fake_s3() as fake:
        client = report_client(tmp_path, fake, report_publish=True)
        _token, submission = complete_submission(client, tmp_path)
        publisher = client.app.state.reports
        known = publisher._known
        publisher._known = lambda sub: KnownResult(
            known(sub).known, [UNCHECKED_ACQUISITION]
        )
        publisher.run_once()
        record = publisher.publications.get(submission.id)
        assert (record.state, record.last_error, record.attempts) == (
            "pending",
            "network",
            1,
        )
        assert record.next_attempt_at > submission.updated_at
        assert "PUT" not in methods(fake)
        assert publisher.run_once() is False


def test_scan_reads_acquisition_values_from_the_database(site) -> None:  # noqa: F811
    created = _create(site, target_player_name="イカすプレイヤー")
    settings = site.app.state.settings
    submission_id = created["submission_id"]
    analysis = settings.root / "analysis" / submission_id
    write_report_fixture(analysis)
    add_media(analysis)
    _plant(analysis, "イカすプレイヤー")
    url = os.environ["PUBLIC_DATABASE_URL"]
    lookup = database_lookup(replace(settings, database_url=url))
    assert lookup is not None
    report = scan(settings, acquisitions=lookup)
    assert report.findings == {"content_policy:known_value:target_player_name": 1}
    assert report.analyses_unchecked == 0
