"""Pure-reader tests for the public operational job trace."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from public_site.job_trace import (
    JobTimestamps,
    SafeRunProvenance,
    TopLevelTiming,
    compute_timing,
    read_job_trace,
    read_llm_calls,
    read_llm_metrics,
    scalar_provenance,
)
from pydantic import ValidationError

from splatoon3_ai_coach.coach.artifacts import (
    CoachingIndex,
    GenerationSetting,
    PrototypeIndex,
    PrototypeIndexEntry,
    PrototypeOutput,
    RunProvenance,
)
from splatoon3_ai_coach.vision.models import (
    AnalysisIdentity,
    VisionManifest,
    VisionTimingMetrics,
)


def _at(seconds: float) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)


def test_timing_uses_top_level_only_and_clamps_rounding_noise() -> None:
    timestamps = JobTimestamps(
        created_at=_at(0),
        queued_at=_at(10),
        processing_started_at=_at(15),
        completed_at=_at(35),
    )
    stages = TopLevelTiming(analyze=10.02, coach_inputs=3, coach_prototype=5, frames=2)

    timing = compute_timing(timestamps, stages)

    assert timing.end_to_end == 35
    assert timing.upload_validation == 10
    assert timing.queue_wait == 5
    assert timing.processing == 20
    assert timing.other == 0
    assert timing.timing_status == "consistent"


def test_materially_negative_other_is_preserved_and_flagged() -> None:
    timing = compute_timing(
        JobTimestamps(processing_started_at=_at(0), completed_at=_at(10)),
        TopLevelTiming(analyze=8, coach_inputs=1, coach_prototype=1, frames=0.06),
    )

    assert timing.other == pytest.approx(-0.06)
    assert timing.timing_status == "timing_inconsistent"


def test_missing_timestamps_and_stage_values_yield_none() -> None:
    timing = compute_timing(
        JobTimestamps(created_at=_at(0)),
        TopLevelTiming(analyze=1, coach_inputs=1, coach_prototype=1),
    )

    assert timing.end_to_end is None
    assert timing.upload_validation is None
    assert timing.queue_wait is None
    assert timing.processing is None
    assert timing.other is None


def test_llm_metrics_are_aggregated_without_error_text(tmp_path: Path) -> None:
    path = tmp_path / "llm_metrics.jsonl"
    rows = [
        {
            "provider": "cursor",
            "model": "model-b",
            "latency_ms": 20,
            "input_tokens_est": 10,
            "output_tokens_est": 5,
            "success": True,
            "skipped_llm": False,
            "error": "secret text",
            "unknown": "also omitted",
        },
        {
            "provider": "cursor",
            "model": "model-a",
            "latency_ms": 3,
            "input_tokens_est": None,
            "output_tokens_est": None,
            "success": False,
            "skipped_llm": False,
            "error": "provider leaked details",
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    summary = read_llm_metrics(path)

    assert summary is not None
    assert summary.model_dump() == {
        "runs": 2,
        "successes": 1,
        "failures": 1,
        "skipped": 0,
        "latency_ms": 23,
        "input_tokens_est": 10,
        "output_tokens_est": 5,
        "providers": ["cursor"],
        "models": ["model-a", "model-b"],
    }
    assert "error" not in summary.model_dump_json()


def test_trace_reads_manifest_versions_and_provenance(tmp_path: Path) -> None:
    analysis = tmp_path / "analysis"
    inputs = analysis / "coach_inputs"
    prototype = analysis / "coach_prototype"
    inputs.mkdir(parents=True)
    prototype.mkdir()
    manifest = VisionManifest(
        analysis=AnalysisIdentity(
            analysis_id="analysis-id",
            package_version="0.2.0",
            git_sha="abc123",
            detector_versions={"timer": "v1"},
            vision_config_sha256="vision-sha",
            video_identity="video-id",
            language="en",
        ),
        video_identity="video-id",
        timing=VisionTimingMetrics(
            video_duration_seconds=60,
            decoded_frame_count=100,
            cadence_frame_count=20,
            decode_seconds=2,
            timer_detector_seconds=3,
            death_detector_seconds=4,
            temporal_seconds=1,
            total_seconds=12,
        ),
    )
    (analysis / "vision_manifest.json").write_text(
        manifest.model_dump_json(), encoding="utf-8"
    )
    (inputs / "coaching_index.json").write_text(
        CoachingIndex(
            evidence_version=4, analysis_id="analysis-id", entries=[]
        ).model_dump_json(),
        encoding="utf-8",
    )
    provenance = RunProvenance(
        provider="cursor",
        model="coach-model",
        temperature=GenerationSetting(requested=0, sent=0, support="supported"),
        seed=GenerationSetting(requested=7, sent=7, support="supported"),
        input_hashes={"system": "hash"},
        executed_at="2026-01-01T00:00:00Z",
        importance_config_hash="abc123def456",
    )
    output = PrototypeOutput(
        model="coach-model",
        output_json="unit.coach-model.output.json",
        raw_txt="unit.coach-model.raw.txt",
        flags_json="unit.coach-model.flags.json",
        skipped_llm=False,
        provenance=provenance,
    )
    entry = PrototypeIndexEntry(
        scenario_id="unit",
        candidate_id="unit",
        candidate_type="death_episode",
        safe_id="unit",
        video_time=1,
        importance_score=1,
        coach_input_json="unit.coach_input.json",
        coaching_json="unit.coaching.json",
        outputs=[output],
        primary_output_json=output.output_json,
    )
    (prototype / "coaching_index.json").write_text(
        PrototypeIndex(
            provider="cursor", primary_model="coach-model", entries=[entry]
        ).model_dump_json(),
        encoding="utf-8",
    )

    trace = read_job_trace(analysis, top_level=TopLevelTiming(analyze=15))

    assert trace.vision is not None
    assert trace.vision.analysis_id == "analysis-id"
    assert trace.coaching_evidence_version == 4
    assert trace.run_provenance[0].provider == "cursor"
    assert trace.run_provenance[0].model == "coach-model"
    assert trace.run_provenance[0].importance_config_hash == "abc123def456"
    assert trace.timing.diagnostic.vision_decode == 2
    assert trace.timing.diagnostic.vision_detectors == 7
    assert trace.timing.diagnostic.fusion == 1
    assert trace.timing.diagnostic.analyze_non_vision == 3


def test_importance_hash_validation_matches_the_12_hex_producer() -> None:
    kept = SafeRunProvenance(
        provider="cursor", model="coach-model", importance_config_hash="abc123def456"
    )
    assert kept.importance_config_hash == "abc123def456"
    with pytest.raises(ValidationError):
        SafeRunProvenance(
            provider="cursor",
            model="coach-model",
            importance_config_hash="a" * 64,
        )


def test_llm_call_rows_drop_scenario_ids_and_errors(tmp_path: Path) -> None:
    path = tmp_path / "llm_metrics.jsonl"
    path.write_text(
        json.dumps(
            {
                "scenario_id": "secret-scenario",
                "provider": "cursor",
                "model": "coach-model",
                "skipped_llm": False,
                "success": False,
                "latency_ms": 4,
                "input_tokens_est": 3,
                "output_tokens_est": None,
                "error": "provider traceback",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    calls = read_llm_calls(path)

    assert calls is not None
    dumped = calls[0].model_dump()
    assert dumped["disposition"] == "requested_failed"
    assert "scenario_id" not in dumped
    assert "error" not in dumped


def test_scalar_provenance_uses_reader_versions(tmp_path: Path) -> None:
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    trace = read_job_trace(analysis)
    scalar = scalar_provenance(trace)
    assert scalar.snapshot_schema_version == 1
    assert scalar.snapshot_reader_version == "job-trace-1"
    assert scalar.importance_config_hash is None


def test_trace_models_forbid_unexpected_fields() -> None:
    with pytest.raises(ValidationError):
        TopLevelTiming.model_validate({"analyze": 1, "error": "do not expose"})
