"""Experiment J stop point 4 runner: fail-closed gate, exactly-once calls,
interruption and resume without duplicates (no model calls)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import coaching_units_experiment_j_run as xrun
import pytest

GOOD = json.dumps(
    {
        "assessment": "Fact.",
        "claims": [],
        "evidence_used": [],
        "limitations": [],
        "recommendations": [],
        "selected_claim_ids": [],
    }
)


class FakeProvider:
    """Scripted replies; ``KeyboardInterrupt`` simulates a killed run."""

    def __init__(self, script=None):
        self.script = dict(script or {})
        self.calls = []

    def generation_settings(self):
        return {}

    def input_hashes(self, system, prompt):
        return {"message_sha256": hashlib.sha256((system + prompt).encode()).hexdigest()}

    def complete(self, system, prompt):
        n = len(self.calls)
        self.calls.append(prompt)
        action = self.script.get(n, GOOD)
        if isinstance(action, BaseException):
            raise action
        return action


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _plan(tmp_path, deaths=3):
    """A small sealed layout: inputs, inputs.sha256 and manifest."""
    out = tmp_path / "j"
    files = {"system_prompt_A.txt": "system A", "system_prompt_M.txt": "system M"}
    calls = []
    for n in range(1, deaths + 1):
        death = f"{n:03d}|death_episode:{n * 10}.000"
        for arm in ("A", "M"):
            rel = f"{arm}/{n:03d}.user_prompt.txt"
            files[rel] = f"prompt {arm} {n}"
            for repeat in (1, 2):
                calls.append(
                    {
                        "call_id": f"{arm}__{n:03d}__r{repeat}",
                        "population": "P1",
                        "arm": arm,
                        "repeat": repeat,
                        "death_id": death,
                        "file": rel,
                        "system": f"system_prompt_{arm}.txt",
                    }
                )
    files["manifest.json"] = json.dumps({"calls": calls, "repeats": 2})
    for rel, text in files.items():
        path = out / "inputs" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    lines = [f"{_sha(t)}  inputs/{rel}" for rel, t in sorted(files.items())]
    (out / "inputs.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out, len(calls)


def _gate(passed=True):
    def gate(config, provider, model, outputs_check, out):
        return {
            "checked_at": "2026-10-21T00:00:00+00:00",
            "provider": provider,
            "model": model,
            "seal_commit": "seal",
            "head_commit": "head",
            "passed": passed and outputs_check["ok"],
            "checks": [outputs_check],
        }

    return gate


def _run(out, provider, passed=True):
    return xrun.run(
        None,
        "cursor",
        "gpt-5.6-luna",
        lambda *a: provider,
        out=out,
        gate_fn=_gate(passed),
    )


def _records(out):
    calls = out / "outputs" / "calls"
    return {p.stem: json.loads(p.read_text()) for p in sorted(calls.glob("*.json"))}


def test_fresh_run_makes_each_planned_call_exactly_once(tmp_path):
    out, total = _plan(tmp_path)
    provider = FakeProvider()
    assert _run(out, provider) == 0
    records = _records(out)
    assert len(provider.calls) == total == len(records)
    record = records["M__002__r2"]
    assert (record["population"], record["arm"], record["repeat"]) == ("P1", "M", 2)
    assert record["user_prompt_sha256"] == _sha("prompt M 2")
    assert record["system_sha256"] == _sha("system M")
    assert record["parse_error"] is None and record["assessment"]["assessment"] == "Fact."
    entries, _ = xrun.read_log(out)
    assert [e["event"] for e in entries] == ["start", "end"] * total
    assert not (out / "outputs" / "run.lock").exists()


def test_run_order_is_repeat_then_death_then_arm(tmp_path):
    out, _ = _plan(tmp_path)
    cells = xrun.expected_cells(xrun.load_manifest(out))
    assert [c.call_id for c in cells[:4]] == [
        "A__001__r1",
        "M__001__r1",
        "A__002__r1",
        "M__002__r1",
    ]
    assert {c.repeat for c in cells[:6]} == {1}


def test_failed_gate_makes_zero_calls(tmp_path):
    out, _ = _plan(tmp_path)
    provider = FakeProvider()
    with pytest.raises(SystemExit, match="0 calls made"):
        _run(out, provider, passed=False)
    assert provider.calls == [] and not (out / "outputs").exists()


def test_interruption_then_resume_never_duplicates_or_overwrites(tmp_path):
    out, total = _plan(tmp_path)
    first = FakeProvider({4: KeyboardInterrupt()})
    with pytest.raises(KeyboardInterrupt):
        _run(out, first)
    done = _records(out)
    assert len(done) == 4 and not (out / "outputs" / "run.lock").exists()
    stamps = {
        k: os.stat(out / "outputs" / "calls" / f"{k}.json").st_mtime_ns for k in done
    }
    second = FakeProvider()
    assert _run(out, second) == 0
    assert len(second.calls) == total - 4
    records = _records(out)
    assert len(records) == total
    for k, stamp in stamps.items():
        assert os.stat(out / "outputs" / "calls" / f"{k}.json").st_mtime_ns == stamp
    interrupted = xrun.expected_cells(xrun.load_manifest(out))[4].call_id
    assert records[interrupted]["attempt"] == 2
    assert len(first.calls) + len(second.calls) == total + 1
    third = FakeProvider()
    assert _run(out, third) == 0 and third.calls == []


def test_provider_error_is_archived_and_only_it_is_recalled(tmp_path):
    out, total = _plan(tmp_path)
    assert _run(out, FakeProvider({1: RuntimeError("boom")})) == 1
    failed = xrun.expected_cells(xrun.load_manifest(out))[1].call_id
    assert _records(out)[failed]["call_error"] == "boom"
    again = FakeProvider()
    assert _run(out, again) == 0 and len(again.calls) == 1
    assert (out / "outputs" / "failed_attempts" / f"{failed}.attempt1.json").exists()
    assert _records(out)[failed]["attempt"] == 2 and len(_records(out)) == total


def test_unparseable_reply_is_a_result_and_never_recalled(tmp_path):
    out, _ = _plan(tmp_path)
    assert _run(out, FakeProvider({0: "not json"})) == 0
    first = xrun.expected_cells(xrun.load_manifest(out))[0].call_id
    assert _records(out)[first]["parse_error"] is not None
    again = FakeProvider()
    assert _run(out, again) == 0 and again.calls == []


def test_resume_refuses_when_a_logged_reply_has_no_record(tmp_path):
    out, _ = _plan(tmp_path)
    _run(out, FakeProvider())
    (out / "outputs" / "calls" / "A__001__r1.json").unlink()
    again = FakeProvider()
    with pytest.raises(SystemExit, match="0 calls made"):
        _run(out, again)
    assert again.calls == []


def test_resume_refuses_records_outside_the_plan_or_with_wrong_identity(tmp_path):
    out, _ = _plan(tmp_path)
    _run(out, FakeProvider())
    calls = out / "outputs" / "calls"
    (calls / "C__999__r1.json").write_text(json.dumps({"call_error": None}))
    with pytest.raises(SystemExit):
        _run(out, FakeProvider())
    (calls / "C__999__r1.json").unlink()
    record = json.loads((calls / "A__001__r1.json").read_text())
    (calls / "A__001__r1.json").write_text(json.dumps({**record, "arm": "M"}))
    again = FakeProvider()
    with pytest.raises(SystemExit):
        _run(out, again)
    assert again.calls == []


def test_write_record_never_replaces_a_reply(tmp_path):
    path = tmp_path / "x.json"
    xrun.write_record(path, {"call_error": "boom"})
    xrun.write_record(path, {"call_error": None})
    with pytest.raises(SystemExit, match="overwrite"):
        xrun.write_record(path, {"call_error": None})


def test_attempt_cap_stops_the_run(tmp_path):
    out, _ = _plan(tmp_path, deaths=1)
    for _ in range(xrun.MAX_ATTEMPTS):
        assert _run(out, FakeProvider({0: RuntimeError("down")})) == 1
    provider = FakeProvider()
    with pytest.raises(SystemExit, match="attempts used"):
        _run(out, provider)
    assert provider.calls == []


def test_changed_sealed_input_stops_before_the_call(tmp_path):
    out, _ = _plan(tmp_path)
    (out / "inputs" / "A" / "001.user_prompt.txt").write_text("tampered")
    provider = FakeProvider()
    with pytest.raises(SystemExit, match="sealed input changed"):
        _run(out, provider)
    assert provider.calls == []


def test_run_lock_refuses_a_live_holder_and_clears_a_dead_one(tmp_path):
    out = tmp_path / "j"
    lock = out / "outputs" / "run.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(str(os.getpid()))
    with pytest.raises(SystemExit, match="another run"):
        xrun.RunLock(out).__enter__()
    lock.write_text("999999")
    with xrun.RunLock(out):
        assert lock.read_text() == str(os.getpid())
    assert not lock.exists()


def test_seal_requires_every_reply_and_writes_once(tmp_path):
    out, total = _plan(tmp_path)
    _run(out, FakeProvider({0: RuntimeError("boom")}))
    with pytest.raises(SystemExit, match="not sealable"):
        xrun.seal_outputs(out)
    _run(out, FakeProvider())
    assert xrun.seal_outputs(out)["calls"] == total
    with pytest.raises(SystemExit, match="sealed"):
        xrun.seal_outputs(out)


# --- the real sealed plan (no calls) ------------------------------------------


def test_real_manifest_cells_match_the_frozen_plan():
    cells = xrun.expected_cells(xrun.load_manifest())
    assert len(cells) == 212 and len({c.call_id for c in cells}) == 212
    by_pop = {p: sum(c.population == p for c in cells) for p in ("P1", "P2", "P3", "C")}
    assert by_pop == {"P1": 140, "P2": 20, "P3": 40, "C": 12}
    assert {(c.population, c.arm) for c in cells} == {
        ("P1", "A"),
        ("P1", "M"),
        ("P2", "A"),
        ("P2", "M"),
        ("P3", "M"),
        ("C", "C"),
    }
    assert all(c.repeat == 1 for c in cells[:106]) and all(
        c.repeat == 2 for c in cells[106:]
    )


def test_real_gate_checks_that_need_no_git_or_rebuild():
    manifest = xrun.load_manifest()
    assert xrun.input_problems() == []
    assert xrun.code_problems() == []
    assert xrun.population_problems() == []
    assert xrun.plan_problems(manifest) == []
    assert xrun.amendment_3_problems() == []
    assert xrun.model_problems(manifest, "cursor", "gpt-5.6-luna") == []
    assert xrun.model_problems(manifest, "cursor", "other-model") != []


def test_runner_never_opens_labels_or_notes():
    source = Path(xrun.__file__).read_text(encoding="utf-8")
    assert "relevance_labels" not in source and "LABELS" not in source
    assert "ideal_coaching_note" not in source
