"""Presentation-time localization of a finished review. English stays canonical.

Two paths, kept separate on purpose:

* Fixed evidence lines (headings, marks, roster/map/special lines, factor
  statements) are structured evidence rendered as text. They are mapped to
  Japanese with deterministic templates; an unknown line stays English.
* The coaching paragraph (``assessment``) is prose. It is translated by the
  localization LLM (``coach.translation``), which sees only that English text.

``localized/coaching.ja.json`` is a **derived translation cache**, never an
analytical source of truth. Nothing in the analysis or coaching pipeline reads
it; deleting it only costs a re-translation. It never replaces English output.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from loguru import logger

from public_site.models import CoachingMoment, LifecycleMark, PublicSubmissionResult
from splatoon3_ai_coach.coach.claim_catalog import NO_RECOMMENDATION_MESSAGE

Translator = Callable[[str], str]

CACHE_DIRNAME = "localized"
CACHE_KIND = "derived_translation_cache"
_MAX_PARALLEL_TRANSLATIONS = 3
_WRITE_LOCK = threading.Lock()
_REVIEW_LOCKS: dict[str, threading.Lock] = {}

_EXACT_JA: dict[str, str] = {
    "Death": "デス",
    "Splat": "キル",
    "Map check": "マップ確認",
    "The player checked the map during recovery.": "復帰待ちの間にマップを確認しています。",
    "Special was ready.": "スペシャルが溜まっていました。",
    "Special was not observed as ready near the death.": (
        "デスの直前にスペシャルが溜まっている様子は確認されませんでした。"
    ),
    "No map check observed before death": "デス前のマップ確認は見られませんでした。",
    "No roster information was available.": "人数の情報はありませんでした。",
    NO_RECOMMENDATION_MESSAGE: "今回の情報から言えるアドバイスはありません。",
    "Your team had fewer players alive than the opponents just before you were splatted.": (
        "デスの直前、味方の生存人数が相手より少ない状態でした。"
    ),
    "Your team had more players alive than the opponents just before you were splatted.": (
        "デスの直前、味方の生存人数が相手より多い状態でした。"
    ),
    "Your special gauge was ready immediately before death.": (
        "デスの直前、スペシャルゲージが溜まっていました。"
    ),
    "No map overlay was observed before death.": "デス前にマップを開いた様子は見られませんでした。",
    "Just before you were splatted, your team needed more counts than the opponents.": (
        "デスの直前、味方の残りカウントは相手より多い状態でした。"
    ),
    "Just before you were splatted, your team needed fewer counts than the opponents.": (
        "デスの直前、味方の残りカウントは相手より少ない状態でした。"
    ),
    "The opponent counter went down in the few seconds before you were splatted.": (
        "デスの直前の数秒間に、相手のカウントが進んでいました。"
    ),
}

_PATTERNS_JA: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Death — (\d+:\d{2}) remaining"), "デス — 残り {0}"),
    (re.compile(r"(\d+(?:\.\d+)?)s out of play"), "{0}秒間 戦線離脱"),
    (re.compile(r"(\d+(?:\.\d+)?)s until respawn"), "復帰まで{0}秒"),
    (
        re.compile(r"Last observed map check: (\d+(?:\.\d+)?)s before death"),
        "最後のマップ確認: デスの{0}秒前",
    ),
    (re.compile(r"Roster at death: (\d+)v(\d+)\."), "デス時の人数: 味方{0} 対 相手{1}。"),
    (
        re.compile(r"Death occurred during the final (\d+) seconds of the match\."),
        "試合の残り{0}秒以内でのデスでした。",
    ),
    (
        re.compile(r"Death occurred during the first (\d+) seconds of the match\."),
        "試合開始から{0}秒以内のデスでした。",
    ),
    (
        re.compile(
            r"Just before you were splatted, your team needed (\d+) more counts "
            r"and the opponents needed (\d+)\."
        ),
        "デスの直前の残りカウントは、味方{0}、相手{1}でした。",
    ),
    (
        re.compile(
            r"Just before you were splatted, your team had (\d+) players? alive "
            r"and the opponents had (\d+)\."
        ),
        "デスの直前の生存人数は、味方{0}人、相手{1}人でした。",
    ),
    (
        re.compile(r"Two consecutive deaths occurred within (\d+(?:\.\d+)?) seconds\."),
        "{0}秒以内に連続でデスしています。",
    ),
    (
        re.compile(r"Two consecutive deaths occurred (\d+(?:\.\d+)?) seconds apart\."),
        "{0}秒の間隔で連続デスしています。",
    ),
]


def build_translator(config_path: Path, provider_override: str = "") -> Translator:
    """Japanese translator on the same provider coach-prototype resolves.

    The provider is built on first use, so a missing API key only affects
    Japanese coaching paragraphs (they fall back to English), not startup.
    """
    state: dict[str, object] = {}
    lock = threading.Lock()

    def translate(text: str) -> str:
        from splatoon3_ai_coach.coach.translation import translate_coaching

        with lock:
            if "provider" not in state:
                state["provider"] = _resolve_provider(config_path, provider_override)
        return translate_coaching(text, "ja", state["provider"])  # type: ignore[arg-type]

    return translate


def _resolve_provider(config_path: Path, provider_override: str):
    from splatoon3_ai_coach.coach.llm_client import normalize_coach_provider
    from splatoon3_ai_coach.coach.llm_runs import build_llm_runs
    from splatoon3_ai_coach.config import load_config
    from splatoon3_ai_coach.config.settings import CoachSettings

    app_config = load_config(config_path)
    coach_settings = CoachSettings()
    name = normalize_coach_provider(
        provider_override or coach_settings.llm_provider or app_config.coach.provider
    )
    runs = build_llm_runs(
        provider_name=name,
        coach_cfg=app_config.coach,
        settings=coach_settings,
        ollama_model=None,
        nvidia_model_override=None,
        also_baseline=False,
        baseline_model_override=None,
    )
    return runs[0].provider


def template_line(text: str, locale: str) -> str | None:
    """Deterministic Japanese for a known evidence line, or ``None`` when unknown."""
    if locale != "ja":
        return None
    exact = _EXACT_JA.get(text)
    if exact is not None:
        return exact
    for pattern, template in _PATTERNS_JA:
        match = pattern.fullmatch(text)
        if match:
            return template.format(*match.groups())
    return None


def localize_line(text: str, locale: str) -> str:
    """Template translation with a deterministic English fallback."""
    return template_line(text, locale) or text


def localize_result(
    result: PublicSubmissionResult,
    locale: str,
    analysis_dir: Path,
    translator: Translator | None,
) -> PublicSubmissionResult:
    """Return ``result`` for display in ``locale``. English input is never modified."""
    if locale != "ja":
        return result
    assessments = _translate_assessments(result.moments, analysis_dir, translator)
    moments = [
        _localize_moment(moment, assessments.get(moment.assessment or ""))
        for moment in result.moments
    ]
    return PublicSubmissionResult(moments=moments)


def _localize_moment(moment: CoachingMoment, assessment_ja: str | None) -> CoachingMoment:
    marks = [
        LifecycleMark(**{**mark.model_dump(), "title": localize_line(mark.title, "ja")})
        for mark in moment.marks
    ]
    translated = assessment_ja is not None
    return moment.model_copy(
        update={
            "statements": [localize_line(line, "ja") for line in moment.statements],
            "heading": _maybe(moment.heading),
            "marks": marks,
            "gaps": [_maybe(gap) for gap in moment.gaps],
            "until_active_again": _maybe(moment.until_active_again),
            "recovery_context": _maybe(moment.recovery_context),
            "context": [localize_line(line, "ja") for line in moment.context],
            "assessment": assessment_ja if translated else moment.assessment,
            "assessment_locale": "ja" if translated or not moment.assessment else "en",
        }
    )


def _maybe(text: str | None) -> str | None:
    return None if text is None else localize_line(text, "ja")


def _translate_assessments(
    moments: list[CoachingMoment], analysis_dir: Path, translator: Translator | None
) -> dict[str, str]:
    """English paragraph -> Japanese. Failed paragraphs are simply absent.

    Serialized per review so overlapping requests reuse one translation.
    """
    with _review_lock(analysis_dir):
        return _translate_locked(moments, analysis_dir, translator)


def _review_lock(analysis_dir: Path) -> threading.Lock:
    key = str(analysis_dir.resolve())
    with _WRITE_LOCK:
        return _REVIEW_LOCKS.setdefault(key, threading.Lock())


def _translate_locked(
    moments: list[CoachingMoment], analysis_dir: Path, translator: Translator | None
) -> dict[str, str]:
    found: dict[str, str] = {}
    pending: list[str] = []
    cache = _read_cache(analysis_dir)
    for moment in moments:
        english = moment.assessment
        if not english or english in found or english in pending:
            continue
        fixed = template_line(english, "ja")
        cached = cache.get(_key(english), {}).get("ja")
        if fixed is not None or cached:
            found[english] = fixed or cached
        else:
            pending.append(english)
    if pending and translator is not None:
        fresh = _run_translations(pending, translator)
        found.update(fresh)
        if fresh:
            _write_cache(analysis_dir, fresh)
    return found


def _run_translations(texts: list[str], translator: Translator) -> dict[str, str]:
    workers = min(_MAX_PARALLEL_TRANSLATIONS, len(texts))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda text: _safe_translate(translator, text), texts))
    return {text: ja for text, ja in zip(texts, results, strict=True) if ja}


def _safe_translate(translator: Translator, text: str) -> str | None:
    try:
        translated = translator(text).strip()
    except Exception as exc:  # noqa: BLE001 — any provider failure falls back to English
        logger.warning("coaching translation failed; showing English: {}", exc)
        return None
    return translated or None


def _key(english: str) -> str:
    return hashlib.sha256(english.encode("utf-8")).hexdigest()


def cache_path(analysis_dir: Path) -> Path:
    """Location of the derived Japanese translation cache for one review."""
    return analysis_dir / CACHE_DIRNAME / "coaching.ja.json"


def _read_cache(analysis_dir: Path) -> dict[str, dict[str, str]]:
    path = cache_path(analysis_dir)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = payload.get("entries") if isinstance(payload, dict) else None
    return entries if isinstance(entries, dict) else {}


def _write_cache(analysis_dir: Path, fresh: dict[str, str]) -> None:
    path = cache_path(analysis_dir)
    with _WRITE_LOCK:
        entries = _read_cache(analysis_dir)
        for english, japanese in fresh.items():
            entries[_key(english)] = {"en": english, "ja": japanese}
        payload = {
            "kind": CACHE_KIND,
            "source": "canonical English assessment",
            "locale": "ja",
            "entries": entries,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
