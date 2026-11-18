"""Review-code HMAC canary: capture, matching, key lifecycle and crash safety."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fake_s3_helpers import fake_s3
from loguru import logger
from public_site.models import Submission
from public_site.report_bundle import BundleError, prepare_bundle
from public_site.report_canary import (
    CANARY_DIRNAME,
    CANARY_RULE,
    EPOCH_NAME,
    KEY_NAME,
    UNCHECKED_CANARY,
    CanaryStore,
    CanaryUnavailable,
    code_digest,
)
from public_site.report_content import (
    ContentFinding,
    KnownValues,
    scan_jpeg,
    scan_json,
    scan_mp4,
)
from public_site.report_known import submission_known_values
from public_site.report_ops import canary_status, scan
from public_site.store import PublicStore
from report_bundle_fixture import add_media, lavfi_clip, write_report_fixture
from report_test_helpers import (
    Clock,
    complete_submission,
    methods,
    report_client,
    report_settings,
)

SID = "e" * 32
CODE = "ABCD-1234-EFGH-5678"
COMPACT = "ABCD1234EFGH5678"
POST_EPOCH = "2099-01-01T00:00:00Z"
PRE_EPOCH = "2000-01-01T00:00:00Z"
PLANT_FILE = "coach_prototype/death_episode_0.5.model.output.json"
RUN = "20261109T120000Z-0000000e"
TOOLS = Path(__file__).resolve().parents[1] / "tools"


def _canary(tmp_path: Path) -> CanaryStore:
    root = tmp_path / "public"
    root.mkdir(exist_ok=True)
    return CanaryStore(root)


def _submission(
    created_at: str = POST_EPOCH, input_type: str = "review_code", sid: str = SID
) -> Submission:
    return Submission(
        id=sid,
        access_token_hash="a" * 64,
        input_type=input_type,
        created_at=created_at,
        updated_at=created_at,
        expires_at="2099-12-31T00:00:00Z",
        status="complete",
    )


def _known(canary: CanaryStore, submission: Submission | None = None) -> KnownValues:
    matcher, checked = canary.matcher(submission or _submission())
    assert checked and matcher is not None
    known = KnownValues()
    known.add_code(CANARY_RULE, matcher)
    return known


def _captured(tmp_path: Path) -> CanaryStore:
    canary = _canary(tmp_path)
    canary.capture(SID, CODE)
    return canary


def _rule(call) -> str | None:
    try:
        call()
    except ContentFinding as exc:
        return exc.rule
    return None


# Detection.


@pytest.mark.parametrize(
    "planted",
    [
        COMPACT,
        CODE,
        "abcd 1234 efgh 5678",
        "abcd-1234-efgh-5678",
        "abcd1234efgh5678",
        "A B C D 1 2 3 4 E F G H 5 6 7 8",
        "ＡＢＣＤ１２３４ＥＦＧＨ５６７８",
        "prefixABCD1234EFGH5678suffix",
    ],
)
def test_code_in_a_json_string_is_caught(tmp_path: Path, planted: str) -> None:
    known = _known(_captured(tmp_path))
    data = json.dumps({"assessment": f"Your code {planted} was used."}).encode()
    assert _rule(lambda: scan_json(data, known)) == CANARY_RULE


def test_unicode_escaped_code_in_json_is_caught(tmp_path: Path) -> None:
    known = _known(_captured(tmp_path))
    escaped = "".join(f"\\u{ord(ch):04x}" for ch in COMPACT)
    data = ('{"note": "' + escaped + '"}').encode()
    assert COMPACT.encode() not in data
    assert _rule(lambda: scan_json(data, known)) == CANARY_RULE


@pytest.mark.parametrize("separator", ["\n", "\t", "\r\n"])
def test_code_split_by_an_escaped_json_whitespace_is_caught(
    tmp_path: Path, separator: str
) -> None:
    known = _known(_captured(tmp_path))
    data = json.dumps({"note": f"ABCD1234{separator}EFGH5678"}).encode()
    assert b"\\u" not in data and b"ABCD1234EFGH5678" not in data
    assert _rule(lambda: scan_json(data, known)) == CANARY_RULE


def test_code_as_a_json_key_is_caught(tmp_path: Path) -> None:
    known = _known(_captured(tmp_path))
    data = json.dumps({COMPACT.lower(): 1}).encode()
    assert _rule(lambda: scan_json(data, known)) == CANARY_RULE


def test_other_codes_and_partial_codes_do_not_match(tmp_path: Path) -> None:
    known = _known(_captured(tmp_path))
    for text in ("ABCD1234EFGH567", "ABCD1234EFGH5679", "ZBCD1234EFGH5678", "a" * 64):
        data = json.dumps({"note": text}).encode()
        assert _rule(lambda data=data: scan_json(data, known)) is None, text


def test_code_in_ordinary_text_is_caught(tmp_path: Path) -> None:
    known = _known(_captured(tmp_path))
    assert _rule(lambda: known.check(b"log line: code=abcd-1234 efgh-5678\n")) == (
        CANARY_RULE
    )
    assert _rule(lambda: known.check_text("Code ABCD 1234 EFGH 5678.")) == CANARY_RULE


def test_code_in_a_bundle_file_path_is_caught(tmp_path: Path) -> None:
    known = _known(_captured(tmp_path))
    analysis = tmp_path / SID
    write_report_fixture(analysis)
    add_media(analysis)
    source = analysis / "coach_inputs" / "coaching_index.json"
    shutil.copy(source, analysis / "coach_inputs" / f"x{COMPACT}.coaching.json")
    with pytest.raises(BundleError) as caught:
        prepare_bundle(
            analysis,
            submission_id=SID,
            run_id=RUN,
            outcome="coached",
            created_at="2026-11-09T12:00:00Z",
            known=known,
            staging=None,
        )
    assert caught.value.rule == CANARY_RULE


def test_code_in_bundle_json_paths_is_caught(tmp_path: Path) -> None:
    known = _known(_captured(tmp_path))
    bundle = {"files": [{"path": f"coach_inputs/{COMPACT}.coaching.json"}]}
    assert _rule(lambda: scan_json(json.dumps(bundle).encode(), known)) == CANARY_RULE


def test_code_in_mp4_metadata_is_caught(tmp_path: Path) -> None:
    known = _known(_captured(tmp_path))
    clean = tmp_path / "clean.mp4"
    lavfi_clip(clean)
    tagged = tmp_path / "tagged.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(clean),
            "-c",
            "copy",
            "-metadata",
            f"comment=code {CODE}",
            str(tagged),
        ],
        check=True,
    )
    data = tagged.read_bytes()
    assert _rule(lambda: scan_mp4(tagged, data, known)) == CANARY_RULE


def test_code_in_a_jpeg_comment_is_caught(tmp_path: Path) -> None:
    known = _known(_captured(tmp_path))
    comment = f"code {CODE}".encode()
    segment = b"\xff\xfe" + (len(comment) + 2).to_bytes(2, "big") + comment
    still = b"\xff\xd8" + segment + b"\xff\xd9"
    assert _rule(lambda: scan_jpeg(still, known)) == CANARY_RULE


# Check rules (report_known).


def _result(canary: CanaryStore, submission: Submission):
    settings = report_settings(canary.public_root.parent, None)
    store = PublicStore(canary.public_root)
    store.save_submission(submission)
    lookup = {submission.id: {"review_code": None}}.get
    return submission_known_values(settings, store, submission, lookup, canary)


def test_post_epoch_submission_with_its_canary_is_checked(tmp_path: Path) -> None:
    result = _result(_captured(tmp_path), _submission())
    assert result.unchecked == []
    assert [rule for rule, _ in result.known.codes] == [CANARY_RULE]


def test_post_epoch_submission_without_a_canary_is_unchecked(tmp_path: Path) -> None:
    canary = _canary(tmp_path)
    canary.ensure_key()
    assert _result(canary, _submission()).unchecked == [UNCHECKED_CANARY]


def test_pre_epoch_submission_is_checked_without_a_canary(tmp_path: Path) -> None:
    """Documented limitation: pre-canary submissions rely on the database code."""
    canary = _canary(tmp_path)
    canary.ensure_key()
    submission = _submission(created_at=PRE_EPOCH)
    result = _result(canary, submission)
    assert result.unchecked == []
    assert result.known.codes == []


def test_a_record_is_used_even_before_the_epoch(tmp_path: Path) -> None:
    canary = _captured(tmp_path)
    result = _result(canary, _submission(created_at=PRE_EPOCH))
    assert result.unchecked == [] and len(result.known.codes) == 1


def test_uploads_need_no_canary(tmp_path: Path) -> None:
    canary = _canary(tmp_path)
    canary.ensure_key()
    assert _result(canary, _submission(input_type="video")).unchecked == []


def test_no_canary_store_leaves_review_code_unchecked(tmp_path: Path) -> None:
    settings = report_settings(tmp_path, None)
    store = PublicStore(settings.root)
    submission = _submission()
    result = submission_known_values(
        settings, store, submission, {SID: {"review_code": None}}.get, None
    )
    assert result.unchecked == [UNCHECKED_CANARY]


# Key failures: unchecked, never clean, and never regenerated.


def _key_path(canary: CanaryStore) -> Path:
    return canary.root / KEY_NAME


def _break_missing(canary: CanaryStore) -> None:
    _key_path(canary).unlink()


def _break_unreadable(canary: CanaryStore) -> None:
    _key_path(canary).chmod(0o000)


def _break_corrupt(canary: CanaryStore) -> None:
    path = _key_path(canary)
    path.chmod(0o600)
    path.write_text("{not json", encoding="utf-8")


def _break_mode(canary: CanaryStore) -> None:
    _key_path(canary).chmod(0o644)


def _break_symlink_key(canary: CanaryStore) -> None:
    path = _key_path(canary)
    moved = canary.public_root / "elsewhere.json"
    path.rename(moved)
    path.symlink_to(moved)


def _break_symlink_dir(canary: CanaryStore) -> None:
    moved = canary.public_root / "elsewhere"
    canary.root.rename(moved)
    canary.root.symlink_to(moved)


def _break_regenerated(canary: CanaryStore) -> None:
    from public_site.report_canary import _encode, key_id_for

    key = os.urandom(32)
    payload = {
        "key": base64.b64encode(key).decode(),
        "key_id": key_id_for(key),
        "created_at": "2026-01-01T00:00:00Z",
    }
    path = _key_path(canary)
    path.unlink()
    path.write_bytes(_encode(payload))
    path.chmod(0o600)


def _break_record(canary: CanaryStore) -> None:
    (canary.root / f"{SID}.json").unlink()


def _break_pending(canary: CanaryStore) -> None:
    canary.stage_correction(SID, "1111-2222-3333-4444")


BREAKS = {
    "missing": _break_missing,
    "chmod_000": _break_unreadable,
    "corrupt": _break_corrupt,
    "wrong_mode": _break_mode,
    "symlinked_key": _break_symlink_key,
    "symlinked_dir": _break_symlink_dir,
    "regenerated_key_id": _break_regenerated,
    "missing_record_after_epoch": _break_record,
    "leftover_pending": _break_pending,
}


@pytest.mark.parametrize("case", list(BREAKS))
def test_key_failures_are_unchecked_and_never_regenerate(
    tmp_path: Path, case: str
) -> None:
    canary = _captured(tmp_path)
    BREAKS[case](canary)
    before = _snapshot(canary)
    assert _result(canary, _submission()).unchecked == [UNCHECKED_CANARY]
    if case not in {"missing_record_after_epoch", "leftover_pending"}:
        with pytest.raises(CanaryUnavailable):
            canary.ensure_key()
        with pytest.raises(CanaryUnavailable):
            canary.capture("f" * 32, CODE)
    assert _snapshot(canary) == before
    _restore_mode(canary)


def test_wrong_owner_is_unchecked(tmp_path: Path, monkeypatch) -> None:
    canary = _captured(tmp_path)
    real = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: real + 1)
    assert canary.matcher(_submission()) == (None, False)
    with pytest.raises(CanaryUnavailable):
        canary.ensure_key()


def test_lost_directory_never_yields_a_new_key(tmp_path: Path) -> None:
    canary = _captured(tmp_path)
    shutil.rmtree(canary.root)
    with pytest.raises(CanaryUnavailable):
        canary.ensure_key()
    assert canary.matcher(_submission()) == (None, False)
    assert canary.matcher(_submission(created_at=PRE_EPOCH)) == (None, False)


def _snapshot(canary: CanaryStore) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for path in sorted(canary.public_root.rglob("*")):
        if path.relative_to(canary.public_root).parts[0] == "submissions":
            continue
        if path.is_file() and not path.is_symlink() and os.access(path, os.R_OK):
            found[str(path.relative_to(canary.public_root))] = path.read_bytes()
    return found


def _restore_mode(canary: CanaryStore) -> None:
    path = _key_path(canary)
    if path.exists() and not path.is_symlink():
        path.chmod(0o600)


# Crash safety and concurrency.

_ENSURE = """
import sys, time
from pathlib import Path
from public_site.report_canary import CanaryStore
go = Path(sys.argv[2])
while not go.exists():
    time.sleep(0.001)
print(CanaryStore(Path(sys.argv[1])).ensure_key().key_id)
"""


def test_concurrent_starts_create_exactly_one_key(tmp_path: Path) -> None:
    root = tmp_path / "public"
    root.mkdir()
    go = tmp_path / "go"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(TOOLS), *sys.path])}
    children = [
        subprocess.Popen(
            [sys.executable, "-c", _ENSURE, str(root), str(go)],
            stdout=subprocess.PIPE,
            env=env,
            text=True,
        )
        for _ in range(4)
    ]
    time.sleep(0.5)
    go.touch()
    ids = {child.communicate(timeout=60)[0].strip() for child in children}
    assert all(child.returncode == 0 for child in children)
    assert len(ids) == 1 and ids != {""}
    names = sorted(p.name for p in (root / CANARY_DIRNAME).iterdir())
    assert names == [".key.lock", KEY_NAME]
    assert CanaryStore(root).load_key().key_id in ids


def test_crash_before_rename_keeps_the_old_record(tmp_path: Path, monkeypatch) -> None:
    import public_site.report_canary as module

    canary = _captured(tmp_path)
    record = canary.root / f"{SID}.json"
    before = record.read_bytes()

    def crash(_src, _dst):
        raise OSError("simulated crash before rename")

    monkeypatch.setattr(module.os, "rename", crash)
    with pytest.raises(CanaryUnavailable):
        canary.capture(SID, "9999-8888-7777-6666")
    monkeypatch.undo()
    assert record.read_bytes() == before
    assert [p.name for p in canary.root.iterdir() if p.name.endswith(".tmp")] == []
    assert _known(canary).codes


def test_leftover_temporary_file_is_ignored_and_pruned(tmp_path: Path) -> None:
    canary = _captured(tmp_path)
    leftover = canary.root / f".{SID}.json.0123456789ab.tmp"
    leftover.write_bytes(b"partial")
    leftover.chmod(0o600)
    assert _known(canary).codes
    store = PublicStore(canary.public_root)
    store.save_submission(_submission())
    assert canary.prune(store, lambda _sid: True) == 0
    assert canary.prune(store, lambda _sid: True, now=time.time() + 601) == 1
    assert not leftover.exists()


def test_interrupted_pending_write_leaves_the_submission_unchecked(
    tmp_path: Path,
) -> None:
    canary = _captured(tmp_path)
    leftover = canary.root / f".{SID}.pending.json.0123456789ab.tmp"
    leftover.write_bytes(b"partial")
    leftover.chmod(0o600)
    assert canary.matcher(_submission()) == (None, False)


def test_files_are_owner_only(tmp_path: Path) -> None:
    os.umask(0o022)
    canary = _captured(tmp_path)
    assert oct(canary.root.stat().st_mode & 0o777) == "0o700"
    for path in (*canary.root.iterdir(), canary.public_root / EPOCH_NAME):
        assert oct(path.stat().st_mode & 0o777) == "0o600", path.name


# Pending corrections and repair.


def _lookup(code: str | None):
    return {SID: {"review_code": code}}.get


def test_pending_correction_is_never_a_valid_canary(tmp_path: Path) -> None:
    canary = _captured(tmp_path)
    canary.stage_correction(SID, "1111-2222-3333-4444")
    assert canary.matcher(_submission()) == (None, False)
    assert canary.matcher(_submission(created_at=PRE_EPOCH)) == (None, False)


@pytest.mark.parametrize(
    ("db_code", "state", "promoted"),
    [
        ("1111-2222-3333-4444", "promotable", True),
        (CODE, "stale", False),
        (None, "no_code", None),
        ("5555-6666-7777-8888", "mismatch", None),
    ],
)
def test_repair_verifies_the_pending_hmac_against_the_database(
    tmp_path: Path, db_code: str | None, state: str, promoted: bool | None
) -> None:
    canary = _captured(tmp_path)
    canary.stage_correction(SID, "1111-2222-3333-4444")
    assert canary.pending_states(_lookup(db_code)) == {SID: state}
    canary.repair(_lookup(db_code))
    pending = canary.root / f"{SID}.pending.json"
    if promoted is None:
        assert pending.exists()
        assert canary.matcher(_submission()) == (None, False)
        return
    assert not pending.exists()
    corrected = "1111-2222-3333-4444" if promoted else CODE
    known = _known(canary)
    assert _rule(lambda: known.check(corrected.encode())) == CANARY_RULE


def test_repair_failures_are_visible_in_canary_status(tmp_path: Path) -> None:
    canary = _captured(tmp_path)
    canary.stage_correction(SID, "1111-2222-3333-4444")
    leftover = canary.root / f".{SID}.pending.json.0123456789ab.tmp"
    leftover.write_bytes(b"partial")
    leftover.chmod(0o600)
    store = PublicStore(canary.public_root)
    store.save_submission(_submission())
    assert canary.pending_states(_lookup("1111-2222-3333-4444")) == {SID: "multiple"}
    canary.repair(_lookup("1111-2222-3333-4444"))
    assert (canary.root / f"{SID}.pending.json").exists()
    status = canary.status(store, _lookup("1111-2222-3333-4444"))
    assert status["pending"] == 1 and status["pending_multiple"] == 1
    assert status["unchecked_review_code_submissions"] == 1
    leftover.unlink()
    status = canary.status(store, None)
    assert status["pending_unverified"] == 1
    failing = canary.status(store, lambda _sid: (_ for _ in ()).throw(OSError("db")))
    assert failing["pending_lookup_failed"] == 1


# Key-loss recovery with an actual backup archive.


def _backup(root: Path, archive: Path) -> None:
    """The documented procedure: tar of report_canary/ and the epoch marker."""
    subprocess.run(
        ["tar", "-C", str(root), "-cpf", str(archive), CANARY_DIRNAME, EPOCH_NAME],
        check=True,
    )


def _restore(root: Path, archive: Path) -> None:
    subprocess.run(["tar", "-C", str(root), "-xpf", str(archive)], check=True)


def _plant(analysis: Path, value: str) -> None:
    path = analysis / PLANT_FILE
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["assessment"] = f"Code {value}."
    path.write_text(json.dumps(payload), encoding="utf-8")


def _review_code_publisher(tmp_path: Path, fake, clock):
    client = report_client(tmp_path, fake, clock=clock, report_publish=True)
    _token, submission = complete_submission(client, tmp_path)
    store = client.app.state.store
    submission.input_type = "review_code"
    store.save_submission(submission)
    settings = client.app.state.settings
    canary = CanaryStore(settings.root)
    canary.capture(submission.id, CODE)
    publisher = client.app.state.reports
    publisher.canary = canary
    lookup = {submission.id: {"review_code": None}}.get
    publisher._known = lambda sub: submission_known_values(
        settings, store, sub, lookup, canary
    )
    return client, publisher, submission, canary


def _retry(publisher, submission_id: str, clock: Clock) -> None:
    def change(record):
        record.state, record.attempts, record.next_attempt_at = "pending", 0, None
        record.blocked_rule = record.last_error = None
        return record

    publisher.publications.update(submission_id, change)
    clock.advance(seconds=1)


def test_key_loss_recovery_from_a_tar_backup(tmp_path: Path) -> None:
    clock = Clock(datetime.now(UTC).replace(microsecond=0))
    with fake_s3() as fake:
        client, publisher, submission, canary = _review_code_publisher(
            tmp_path, fake, clock
        )
        root, archive = canary.public_root, tmp_path / "canary-backup.tar"
        _backup(root, archive)
        analysis = Path(submission.analysis_dir)
        original = (analysis / PLANT_FILE).read_bytes()
        # 1. Key and canary present: the planted code is caught.
        _plant(analysis, "abcd 1234 efgh 5678")
        assert publisher.run_once() is True
        record = publisher.publications.get(submission.id)
        assert (record.state, record.blocked_rule) == ("blocked", CANARY_RULE)
        # 2. Key missing: fail closed, no PUT, status reports, restart keeps it lost.
        _key_path(canary).unlink()
        _retry(publisher, submission.id, clock)
        assert publisher.run_once() is True
        record = publisher.publications.get(submission.id)
        assert (record.state, record.last_error) == ("pending", "network")
        assert "PUT" not in methods(fake)
        status = canary_status(client.app.state.settings)
        assert (status["key_present"], status["key_valid"]) == (False, False)
        assert status["unchecked_review_code_submissions"] == 1
        publisher.startup()
        with pytest.raises(CanaryUnavailable):
            canary.ensure_key()
        assert not _key_path(canary).exists()
        # 3a. Restored with the wrong mode: still rejected until corrected.
        _restore(root, archive)
        _key_path(canary).chmod(0o644)
        assert canary.matcher(submission) == (None, False)
        _key_path(canary).chmod(0o600)
        # 3b. Restored: the planted code is caught again, then a clean report publishes.
        _retry(publisher, submission.id, clock)
        assert publisher.run_once() is True
        record = publisher.publications.get(submission.id)
        assert (record.state, record.blocked_rule) == ("blocked", CANARY_RULE)
        (analysis / PLANT_FILE).write_bytes(original)
        _retry(publisher, submission.id, clock)
        assert publisher.run_once() is True
        assert publisher.publications.get(submission.id).state == "verified"
        assert "PUT" in methods(fake)
        client.close()


def test_backup_archive_preserves_owner_only_modes(tmp_path: Path) -> None:
    canary = _captured(tmp_path)
    archive = tmp_path / "backup.tar"
    _backup(canary.public_root, archive)
    with tarfile.open(archive) as handle:
        modes = {item.name: item.mode & 0o777 for item in handle.getmembers()}
    assert modes[CANARY_DIRNAME] == 0o700
    assert modes[f"{CANARY_DIRNAME}/{KEY_NAME}"] == 0o600
    assert modes[EPOCH_NAME] == 0o600


def test_publisher_sends_no_put_for_each_key_failure(tmp_path: Path) -> None:
    clock = Clock(datetime.now(UTC).replace(microsecond=0))
    with fake_s3() as fake:
        client, publisher, submission, canary = _review_code_publisher(
            tmp_path, fake, clock
        )
        _key_path(canary).write_text("{corrupt", encoding="utf-8")
        assert publisher.run_once() is True
        record = publisher.publications.get(submission.id)
        assert (record.state, record.last_error) == ("pending", "network")
        assert "PUT" not in methods(fake)
        client.close()


# Logging privacy.


def test_logs_never_contain_the_code_key_or_hmac(tmp_path: Path) -> None:
    messages: list[str] = []
    handler = logger.add(messages.append, format="{message}", level="DEBUG")
    try:
        canary = _captured(tmp_path)
        canary.stage_correction(SID, "1111-2222-3333-4444")
        canary.repair(_lookup("1111-2222-3333-4444"))
        canary.stage_correction(SID, "2222-3333-4444-5555")
        canary.repair(_lookup(None))
        _key_path(canary).chmod(0o644)
        canary.matcher(_submission())
        _key_path(canary).chmod(0o600)
        key = canary.load_key()
    finally:
        logger.remove(handler)
    text = "\n".join(messages)
    forbidden = [
        CODE,
        COMPACT,
        "1111-2222-3333-4444",
        "1111222233334444",
        "2222333344445555",
        base64.b64encode(key.key).decode(),
        key.key.hex(),
        code_digest(key.key, CODE).hex(),
        code_digest(key.key, "1111-2222-3333-4444").hex(),
    ]
    assert messages
    assert not [value for value in forbidden if value in text]
    assert "key=" not in repr(key) and key.key.hex() not in repr(key)


def test_scan_reports_the_canary_group(tmp_path: Path) -> None:
    settings = report_settings(tmp_path, None)
    store = PublicStore(settings.root)
    store.save_submission(_submission())
    analysis = settings.root / "analysis" / SID
    write_report_fixture(analysis)
    add_media(analysis)
    canary = CanaryStore(settings.root)
    canary.capture(SID, CODE)
    lookup = {SID: {"review_code": None}}.get
    assert scan(settings, acquisitions=lookup).findings == {}
    _plant(analysis, CODE)
    assert scan(settings, acquisitions=lookup).findings == {
        f"content_policy:{CANARY_RULE}": 1
    }
    (canary.root / f"{SID}.json").unlink()
    report = scan(settings, acquisitions=lookup)
    assert report.unchecked == {UNCHECKED_CANARY: 1}
    assert report.analyses_unchecked == 1
