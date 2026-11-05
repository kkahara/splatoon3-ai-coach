"""Blinded evaluation uses copies only; never opens a policy key."""

import csv
import shutil
from pathlib import Path

import pytest
from coaching_diversity_review import COLUMNS, Edit, ReviewStore
from coaching_report_diversity_run import MEASURES, _rating, sheet_columns

from splatoon3_ai_coach.config.paths import PROJECT_ROOT

REAL = PROJECT_ROOT / "analysis/importance_report/coaching_report_diversity/blind"


@pytest.fixture
def store(tmp_path):
    """Copy only allowlisted blinded packet files."""
    if not REAL.exists():
        pytest.skip("Local blinded packets unavailable")
    for name in ("rating_sheet.csv", "INSTRUCTIONS.md"):
        shutil.copyfile(REAL / name, tmp_path / name)
    for i in range(1, 10):
        (tmp_path / f"R{i}").mkdir()
        for side in "AB":
            shutil.copyfile(REAL / f"R{i}/{side}.md", tmp_path / f"R{i}/{side}.md")
    return ReviewStore(tmp_path)


def edit(store, field, value, kind="rating"):
    """Submit one fresh explicit edit."""
    store.edit(
        Edit(
            revision=store.snapshot()["revision"],
            blind_id="R1",
            field=field,
            value=value,
            kind=kind,
        )
    )


def test_nine_pairs_blinded_and_japanese(store, monkeypatch):
    original = Path.open
    opened = []

    def guarded(path, *args, **kwargs):
        opened.append(path)
        assert store.blind in path.parents
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    packet = store.blind / "R1/A.md"
    packet.write_text(packet.read_text() + "\n日本語のコーチング\n", encoding="utf-8")
    resumed = ReviewStore(store.blind)
    data = resumed.snapshot()
    assert len(data["reports"]) == 9
    for bid, reports in data["reports"].items():
        for side in "AB":
            assert reports[side] == (store.blind / bid / f"{side}.md").read_text()
    assert "日本語" in data["reports"]["R1"]["A"]
    assert all("key" not in p.parts for p in opened)
    assert all(word not in str(data) for word in ("capped", "current", "rating_key"))


def test_save_resume_partial_backup_and_atomic(store, monkeypatch):
    import coaching_diversity_review as review

    before = store.sheet.read_bytes()
    edit(store, "A_usefulness_1to5", "4")
    assert (store.blind / "rating_sheet.pre_ui.csv").read_bytes() == before
    edit(store, "notes", "日本語, memo\nsecond line")
    resumed = ReviewStore(store.blind)
    row = resumed.snapshot()["rows"][0]
    assert row["A_usefulness_1to5"] == "4"
    assert row["B_usefulness_1to5"] == ""
    assert row["notes"] == "日本語, memo\nsecond line"
    saved = store.sheet.read_bytes()

    def failure(*args):
        raise OSError("interrupted replace")

    monkeypatch.setattr(review.os, "replace", failure)
    with pytest.raises(OSError):
        edit(store, "A_usefulness_1to5", "2")
    assert store.sheet.read_bytes() == saved
    assert not list(store.blind.glob(".review-*"))


def test_validation_conflict_and_pending(store):
    before = store.sheet.read_bytes()
    for field, value in [
        ("A_usefulness_1to5", "6"),
        ("B_evidence_supported_yn", "maybe"),
        ("preferred_A_B_tie", "C"),
        ("blind_id", "R2"),
    ]:
        with pytest.raises(ValueError):
            edit(store, field, value)
    assert before == store.sheet.read_bytes()
    rev = store.snapshot()["revision"]
    edit(store, "A_usefulness_1to5", "3")
    with pytest.raises(ValueError):
        store.edit(Edit(revision=rev, blind_id="R1", field="notes", value="stale"))
    edit(store, "A_usefulness_1to5", "Check observation", "pending")
    state = ReviewStore(store.blind).snapshot()
    assert state["pending"][0]["field"] == "A_usefulness_1to5"
    assert state["rows"][0]["A_usefulness_1to5"] == "3"
    assert state["queue"] and all(not q["available"] for q in state["queue"])
    edit(store, "A_usefulness_1to5", "", "pending")
    assert store.snapshot()["pending"] == []


def test_analysis_contract_without_unblinding(store):
    assert COLUMNS == sheet_columns()
    for name, scale in MEASURES:
        value = "y" if scale == "yn" else "4"
        edit(store, f"A_{name}_{scale}", value)
        edit(store, f"B_{name}_{scale}", value)
    edit(store, "preferred_A_B_tie", "tie")
    with store.sheet.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 9
    for side in "AB":
        for name, scale in MEASURES:
            assert _rating(rows[0][f"{side}_{name}_{scale}"], scale) in (1, 4)


def test_symlink_and_duplicate_refused(store, tmp_path):
    packet = store.blind / "R1/A.md"
    packet.unlink()
    packet.symlink_to(store.blind / "R1/B.md")
    with pytest.raises(ValueError, match="Symlinks"):
        ReviewStore(store.blind)
    packet.unlink()
    packet.write_text("# Report A")
    text = store.sheet.read_text().replace("R2,", "R1,")
    store.sheet.write_text(text)
    with pytest.raises(ValueError, match="duplicate"):
        ReviewStore(store.blind)


def test_http_routes_and_write_token(store):
    """Exercise the actual server against copied data, including blocked paths."""
    import json
    import subprocess
    import sys
    import time
    import urllib.error
    import urllib.request

    script = PROJECT_ROOT / "tools/reports/coaching_report_diversity.py"
    # The CLI expects OUT/blind, so use a separate copy under that layout.
    out = store.blind / "http-test"
    shutil.copytree(
        store.blind, out / "blind", ignore=shutil.ignore_patterns("http-test")
    )
    video = out / "test.mov"
    video.write_bytes(b"0123456789")
    image = out / "test.jpg"
    image.write_bytes(b"fake jpeg")
    (out / "review_ui_media.json").write_text(
        json.dumps(
            {
                "R1": {
                    "video": str(video),
                    "moments": {"Death — 4:08 remaining": 42.5},
                    "images": {"Death — 4:08 remaining": str(image)},
                }
            }
        )
    )
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen(
        [
            sys.executable,
            str(script),
            "--out",
            str(out),
            "review-ui",
            "--port",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(base + "/api", timeout=1) as response:
                    data = json.load(response)
                break
            except OSError:
                time.sleep(0.05)
        else:
            pytest.fail("Review server did not start")
        assert len(data["reports"]) == 9
        assert data["media"]["R1"]["available"]
        request = urllib.request.Request(
            base + "/media/R1", headers={"Range": "bytes=2-5"}
        )
        with urllib.request.urlopen(request) as response:
            assert response.status == 206
            assert response.read() == b"2345"
        with urllib.request.urlopen(base + "/image/R1/0") as response:
            assert response.read() == b"fake jpeg"
        for route in ("/", "/app.js", "/style.css"):
            with urllib.request.urlopen(base + route) as response:
                assert response.status == 200
                assert response.headers["Cache-Control"] == "no-store"
        for route in (
            "/key/rating_key.csv",
            "/../key/rating_key.csv",
            "/rating_sheet.csv",
        ):
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(base + route)
            assert exc.value.code == 404
        body = dict(
            revision=data["revision"],
            blind_id="R1",
            field="notes",
            value="HTTP resume 日本語",
        )
        request = urllib.request.Request(
            base + "/api", json.dumps(body).encode(), method="POST"
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request)
        assert exc.value.code == 403
        request.add_header("X-Review-Token", data["token"])
        with urllib.request.urlopen(request) as response:
            assert response.status == 200
        assert ReviewStore(out / "blind").snapshot()["rows"][0]["notes"] == body["value"]
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_simple_reason_preserves_sheet_and_preference_pending(store):
    """Simple responses never infer the detailed scores or erase existing ones."""
    edit(store, "A_usefulness_1to5", "5")
    before = store.sheet.read_bytes()
    edit(store, "reason", "specific", "reason")
    edit(store, "preferred_A_B_tie", "Gameplay evidence requested", "pending")
    state = ReviewStore(store.blind).snapshot()
    assert store.sheet.read_bytes() == before
    assert state["reasons"]["R1"] == "specific"
    assert state["pending"][0]["field"] == "preferred_A_B_tie"
    assert state["rows"][0]["B_usefulness_1to5"] == ""
    with pytest.raises(ValueError):
        edit(store, "reason", "invented", "reason")


def test_verified_media_metadata_and_missing_queue(store):
    """Only neutral IDs are exposed; removing a source queues its moment."""
    import json

    video = store.blind / "recording.mov"
    image = store.blind / "image.jpg"
    video.write_bytes(b"video contents")
    image.write_bytes(b"image contents")
    import re

    title = re.search(r"^### (Death — .+)$", store.reports["R1"]["A"], re.M).group(1)
    metadata = {
        "R1": {
            "video": str(video),
            "video_identity": "private-identity",
            "moments": {title: 42.5},
            "images": {title: str(image)},
        }
    }
    (store.blind.parent / "review_ui_media.json").write_text(json.dumps(metadata))
    resumed = ReviewStore(store.blind)
    state = resumed.snapshot()
    assert state["media"]["R1"]["available"]
    assert state["media"]["R1"]["moments"][title] == 42.5
    assert state["media"]["R1"]["images"][title] == "/image/R1/0"
    assert str(video) not in str(state) and "private-identity" not in str(state)
    assert not any(q["blind_id"] == "R1" and q["moment"] == title for q in state["queue"])
    video.unlink()
    assert any(
        q["blind_id"] == "R1" and q["moment"] == title and q["needed"] == "Gameplay video"
        for q in resumed.snapshot()["queue"]
    )


def test_simple_ui_renders_and_records_pending():
    """Exercise the browser script with a minimal DOM and a fake save API."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    result = subprocess.run(
        [node, str(PROJECT_ROOT / "tests/coaching_diversity_ui_check.cjs")],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "interaction passed" in result.stdout
