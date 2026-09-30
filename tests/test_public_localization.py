"""Japanese display of a finished review: templates, translation, sidecar cache."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from public_site import localize
from public_site.app import create_app
from public_site.localize import (
    CACHE_KIND,
    cache_path,
    localize_line,
    localize_result,
    template_line,
)
from public_site.media_probe import ProbeFacts
from public_site.models import CoachingMoment, LifecycleMark, PublicSubmissionResult
from public_site.results import load_result
from public_site.settings import PublicSettings
from public_site.store import PublicStore

from splatoon3_ai_coach.config.paths import default_config_path

STATEMENT = "No map overlay was observed before death."
STATEMENT_JA = "デス前にマップを開いた様子は見られませんでした。"
_REPO = Path(__file__).resolve().parents[1]


class _Translator:
    """Fake localization LLM: counts calls, can fail on chosen paragraphs."""

    def __init__(self, fail: set[str] | None = None, delay: float = 0.0) -> None:
        self.fail = fail or set()
        self.delay = delay
        self.calls: list[str] = []
        self.in_flight = 0
        self.peak = 0
        self._lock = threading.Lock()

    def __call__(self, text: str) -> str:
        with self._lock:
            self.calls.append(text)
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        try:
            if self.delay:
                time.sleep(self.delay)
            if text in self.fail:
                raise RuntimeError("provider unavailable")
            return f"（訳）{text}"
        finally:
            with self._lock:
                self.in_flight -= 1


def _seed(analysis: Path, assessments: dict[str, str | None]) -> None:
    """Write coach_inputs + coach_prototype for one death moment per safe id."""
    inputs = analysis / "coach_inputs"
    prototype = analysis / "coach_prototype"
    inputs.mkdir(parents=True, exist_ok=True)
    prototype.mkdir(parents=True, exist_ok=True)
    entries = []
    for rank, (safe_id, assessment) in enumerate(assessments.items(), start=1):
        entries.append(
            {
                "candidate_type": "death_episode",
                "safe_id": safe_id,
                "video_time": float(10 * rank),
                "rank": rank,
                "coaching_json": f"{safe_id}.coaching.json",
            }
        )
        factors = {"factors": [{"active": True, "statement_player": STATEMENT}]}
        coaching = inputs / f"{safe_id}.coaching.json"
        coaching.write_text(json.dumps(factors), encoding="utf-8")
        if assessment is not None:
            (prototype / f"{safe_id}.model.output.json").write_text(
                json.dumps({"assessment": assessment}), encoding="utf-8"
            )
    (inputs / "coaching_index.json").write_text(json.dumps(entries), encoding="utf-8")


def _snapshot(analysis: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(analysis)): path.read_bytes()
        for folder in ("coach_inputs", "coach_prototype")
        for path in sorted((analysis / folder).rglob("*"))
        if path.is_file()
    }


def _moment(assessment: str | None, video_time: float = 10.0) -> CoachingMoment:
    return CoachingMoment(
        scenario_type="death_episode",
        video_time=video_time,
        statements=[STATEMENT],
        assessment=assessment,
        heading="Death — 1:24 remaining",
        marks=[
            LifecycleMark(
                label="death", title="Death", video_time=video_time, anchor=True
            ),
            LifecycleMark(label="splat", title="Splat", video_time=video_time + 2),
        ],
        until_active_again="9.0s out of play",
        recovery_context="The player checked the map during recovery.",
        context=["Roster at death: 2v4.", "Special was ready."],
    )


def test_english_is_returned_unchanged_without_translation(tmp_path: Path) -> None:
    _seed(tmp_path, {"a": "Check the map before pushing."})
    translator = _Translator()
    english = load_result(tmp_path)
    assert localize_result(english, "en", tmp_path, translator) is english
    assert translator.calls == []
    assert not cache_path(tmp_path).exists()


def test_japanese_view_translates_and_reuses_the_sidecar(tmp_path: Path) -> None:
    _seed(tmp_path, {"a": "Check the map before pushing."})
    translator = _Translator()
    first = localize_result(load_result(tmp_path), "ja", tmp_path, translator)
    second = localize_result(load_result(tmp_path), "ja", tmp_path, translator)
    moment = first.moments[0]
    assert moment.assessment == "（訳）Check the map before pushing."
    assert moment.assessment_locale == "ja"
    assert moment.statements == [STATEMENT_JA]
    assert second == first
    assert translator.calls == ["Check the map before pushing."]


def test_english_artifacts_are_byte_identical_after_japanese_view(tmp_path: Path) -> None:
    _seed(tmp_path, {"a": "Check the map.", "b": "Nice retreat."})
    before = _snapshot(tmp_path)
    localize_result(load_result(tmp_path), "ja", tmp_path, _Translator())
    assert _snapshot(tmp_path) == before
    assert load_result(tmp_path).moments[0].assessment == "Check the map."


def test_structure_is_equal_across_locales(tmp_path: Path) -> None:
    english = PublicSubmissionResult(
        moments=[_moment("Stay back.", 10.0), _moment(None, 30.0)]
    )
    japanese = localize_result(english, "ja", tmp_path, _Translator())
    assert len(japanese.moments) == len(english.moments)
    for en, ja in zip(english.moments, japanese.moments, strict=True):
        assert ja.scenario_type == en.scenario_type
        assert ja.video_time == en.video_time
        assert ja.frame == en.frame
        assert [m.video_time for m in ja.marks] == [m.video_time for m in en.marks]
        assert [m.label for m in ja.marks] == [m.label for m in en.marks]
        assert [m.anchor for m in ja.marks] == [m.anchor for m in en.marks]
    moment = japanese.moments[0]
    assert moment.heading == "デス — 残り 1:24"
    assert [m.title for m in moment.marks] == ["デス", "キル"]
    assert moment.until_active_again == "9.0秒間 戦線離脱"
    assert moment.recovery_context == "復帰待ちの間にマップを確認しています。"
    assert moment.context == [
        "デス時の人数: 味方2 対 相手4。",
        "スペシャルが溜まっていました。",
    ]


_PATTERN_SAMPLES = [
    "Death — 2:20 remaining",
    "9.0s out of play",
    "0.0s until respawn",
    "Last observed map check: 61.5s before death",
    "Roster at death: 2v4.",
    "Death occurred during the final 30 seconds of the match.",
    "Death occurred during the first 20 seconds of the match.",
    "Just before you were splatted, your team needed 12 more counts "
    "and the opponents needed 40.",
    "Just before you were splatted, your team had 1 player alive "
    "and the opponents had 3.",
    "Just before you were splatted, your team had 2 players alive "
    "and the opponents had 4.",
    "Two consecutive deaths occurred within 15 seconds.",
    "Two consecutive deaths occurred 7.5 seconds apart.",
]


@pytest.mark.parametrize("line", [*localize._EXACT_JA, *_PATTERN_SAMPLES])
def test_known_evidence_lines_have_japanese_templates(line: str) -> None:
    japanese = template_line(line, "ja")
    assert japanese is not None
    assert japanese != line
    assert re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", japanese)


def test_unknown_line_falls_back_to_english() -> None:
    line = "A brand-new evidence line nobody has templated."
    assert template_line(line, "ja") is None
    assert localize_line(line, "ja") == line
    assert template_line("Death", "en") is None


def test_one_failed_translation_leaves_the_rest_japanese(tmp_path: Path) -> None:
    english = PublicSubmissionResult(
        moments=[_moment("Paragraph one.", 10.0), _moment("Paragraph two.", 30.0)]
    )
    translator = _Translator(fail={"Paragraph two."})
    japanese = localize_result(english, "ja", tmp_path, translator)
    ok, failed = japanese.moments
    assert ok.assessment == "（訳）Paragraph one."
    assert ok.assessment_locale == "ja"
    assert failed.assessment == "Paragraph two."
    assert failed.assessment_locale == "en"
    for moment in (ok, failed):
        assert moment.heading == "デス — 残り 1:24"
        assert [m.title for m in moment.marks] == ["デス", "キル"]
        assert moment.statements == [STATEMENT_JA]
    cached = json.loads(cache_path(tmp_path).read_text(encoding="utf-8"))
    stored = [entry["en"] for entry in cached["entries"].values()]
    assert stored == ["Paragraph one."]


def test_shared_paragraph_is_translated_once(tmp_path: Path) -> None:
    english = PublicSubmissionResult(
        moments=[_moment("Same advice.", float(t)) for t in (10, 20, 30)]
    )
    translator = _Translator()
    japanese = localize_result(english, "ja", tmp_path, translator)
    assert translator.calls == ["Same advice."]
    assert {m.assessment for m in japanese.moments} == {"（訳）Same advice."}


def test_at_most_three_translations_run_at_once(tmp_path: Path) -> None:
    english = PublicSubmissionResult(
        moments=[_moment(f"Advice number {i}.", float(i)) for i in range(8)]
    )
    translator = _Translator(delay=0.05)
    localize_result(english, "ja", tmp_path, translator)
    assert len(translator.calls) == 8
    assert 1 < translator.peak <= 3


def test_overlapping_requests_share_one_translation(tmp_path: Path) -> None:
    _seed(tmp_path, {"a": "Hold the high ground."})
    translator = _Translator(delay=0.1)
    threads = [
        threading.Thread(
            target=localize_result,
            args=(load_result(tmp_path), "ja", tmp_path, translator),
        )
        for _ in range(3)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert translator.calls == ["Hold the high ground."]


def test_template_assessment_needs_no_translator(tmp_path: Path) -> None:
    from splatoon3_ai_coach.coach.claim_catalog import NO_RECOMMENDATION_MESSAGE

    english = PublicSubmissionResult(moments=[_moment(NO_RECOMMENDATION_MESSAGE)])
    translator = _Translator()
    moment = localize_result(english, "ja", tmp_path, translator).moments[0]
    assert translator.calls == []
    assert moment.assessment_locale == "ja"
    assert moment.assessment == "今回の情報から言えるアドバイスはありません。"


def test_sidecar_is_marked_as_derived_cache(tmp_path: Path) -> None:
    english = PublicSubmissionResult(moments=[_moment("Stay back.")])
    localize_result(english, "ja", tmp_path, _Translator())
    payload = json.loads(cache_path(tmp_path).read_text(encoding="utf-8"))
    assert payload["kind"] == CACHE_KIND == "derived_translation_cache"
    assert payload["locale"] == "ja"
    assert cache_path(tmp_path).parent.name == "localized"


def test_no_pipeline_module_reads_the_translation_cache() -> None:
    reference = re.compile(r"""["']localized(/|["'])|coaching\.ja\.json""")
    sources = [
        *sorted((_REPO / "src" / "splatoon3_ai_coach").rglob("*.py")),
        _REPO / "tools" / "public_site" / "results.py",
    ]
    offenders = [
        str(path.relative_to(_REPO))
        for path in sources
        if reference.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def _client(
    tmp_path: Path, translator: _Translator, calls: list[list[str]]
) -> TestClient:
    root = tmp_path / "public"
    root.mkdir(parents=True, exist_ok=True)
    settings = PublicSettings(
        root=root,
        config_path=default_config_path(),
        static_dir=tmp_path / "no-dist",
        dev_mode=True,
        worker_id="worker-test",
        public_base_url="http://coach.example",
        max_video_bytes=1000,
    )

    def run(argv: list[str]) -> int:
        calls.append(argv)
        if "coach-inputs" in argv:
            analysis = Path(argv[argv.index("coach-inputs") + 1])
            _seed(analysis, {"a": "Check the map.", "b": "Nice retreat."})
        return 0

    app = create_app(
        settings,
        store=PublicStore(settings.root),
        verifier=lambda token: token == "ok",
        probe=lambda _path: ProbeFacts(12.0, 1920, 1080, "mp4"),
        run_command=run,
        translator=translator,
        start_worker=False,
    )
    client = TestClient(app)
    client.app = app
    return client


def _finished_token(client: TestClient) -> str:
    created = client.post(
        "/api/submissions",
        json={
            "turnstile_token": "ok",
            "filename": "match.mp4",
            "size_bytes": 8,
            "content_type": "video/mp4",
            "language": "en",
        },
    ).json()
    upload = created["upload"]
    client.put(upload["url"], content=b"12345678", headers=upload["headers"])
    client.post(f"/api/submissions/{created['token']}/uploaded")
    assert client.app.state.worker.pump() is True
    return created["token"]


def test_locale_switch_uses_the_api_without_rerunning_the_worker(tmp_path: Path) -> None:
    translator = _Translator()
    calls: list[list[str]] = []
    client = _client(tmp_path, translator, calls)
    token = _finished_token(client)
    calls.clear()

    english = client.get(f"/api/submissions/{token}").json()
    assert english["locale"] == "en"
    assert english["result"]["moments"][0]["assessment"] == "Check the map."
    assert english["result"]["moments"][0]["assessment_locale"] == "en"
    assert translator.calls == []

    japanese = client.get(f"/api/submissions/{token}?locale=ja").json()
    again = client.get(f"/api/submissions/{token}?locale=ja").json()
    back = client.get(f"/api/submissions/{token}?locale=en").json()
    assert japanese["locale"] == "ja"
    assert [m["assessment"] for m in japanese["result"]["moments"]] == [
        "（訳）Check the map.",
        "（訳）Nice retreat.",
    ]
    assert again["result"] == japanese["result"]
    assert back["result"] == english["result"]
    assert sorted(translator.calls) == ["Check the map.", "Nice retreat."]
    assert calls == []


def test_unsupported_locale_is_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path, _Translator(), [])
    token = _finished_token(client)
    assert client.get(f"/api/submissions/{token}?locale=fr").status_code == 422
