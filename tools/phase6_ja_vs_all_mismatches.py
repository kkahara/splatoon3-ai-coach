"""Phase 6: explain JA-only vs ALL mismatches on 2026-09-07 09-17-30.

Optimized: language-neutral detectors (timer/death/active/map) run once;
only respawn+splat are compared ALL vs JA. Read-only investigation.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from loguru import logger

logger.disable("splatoon3_ai_coach")

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import VisionLanguage
from splatoon3_ai_coach.vision.death import _load_language_cue_templates
from splatoon3_ai_coach.vision.events import infer_events
from splatoon3_ai_coach.vision.models import DetectorResult, GameEvent, VisionFrameResult
from splatoon3_ai_coach.vision.registry import build_detectors
from splatoon3_ai_coach.vision.respawn import (
    _SCALES,
    _STRONG_TEMPLATE_FLOOR,
    _fit_template_to_size,
    _load_flat_templates,
    _to_gray,
    _white_mask_bgr,
)
from splatoon3_ai_coach.vision.roi import crop_roi
from splatoon3_ai_coach.vision.splat import (
    _TEMPLATE_SCALES,
    _is_text_template,
    _load_classified_templates,
)
from splatoon3_ai_coach.vision.state import fuse_game_state

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "analysis" / "phase6_ja_vs_all_mismatches.json"
FRAMES = sorted((REPO / "analysis/2026-09-07 09-17-30/debug_snapshots").glob("*.jpg"))
CFG = load_config(default_config_path())


def log(msg: str) -> None:
    print(msg, flush=True)


@dataclass
class NamedTpl:
    name: str
    lang: str
    image: np.ndarray


def load_named_flat(base: Path, langs: tuple[str, ...]) -> list[NamedTpl]:
    out: list[NamedTpl] = []
    for lang in langs:
        for path in sorted((base / lang).glob("*")):
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                continue
            img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if img is None or img.size == 0:
                continue
            out.append(NamedTpl(path.name, lang, img))
    return out


def load_named_splat(
    base: Path, langs: tuple[str, ...]
) -> tuple[list[NamedTpl], list[NamedTpl]]:
    icons: list[NamedTpl] = []
    texts: list[NamedTpl] = []
    for lang in langs:
        for path in sorted((base / lang).glob("*")):
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                continue
            img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if img is None or img.size == 0:
                continue
            item = NamedTpl(path.name, lang, img)
            (texts if _is_text_template(path) else icons).append(item)
    return icons, texts


def pack_lang_detectors(mode: str):
    """Build respawn+splat only; mode all|ja expands templates for all."""
    cfg = load_config(default_config_path())
    cfg.vision.language = VisionLanguage.JA
    cfg.vision.enabled_detectors = ["respawn", "splat"]
    dets = {d.name: d for d in build_detectors(cfg.vision)}
    if mode == "all":
        r = dets["respawn"]
        r._templates = []
        r._prepared = None
        for code in ("en", "ja"):
            r._templates.extend(_load_flat_templates(r.config.template_dir / code))
        s = dets["splat"]
        icons: list = []
        texts: list = []
        for code in ("en", "ja"):
            i, t = _load_classified_templates(s.config.template_dir / code)
            icons.extend(i)
            texts.extend(t)
        s._icon_templates = icons
        s._text_templates = texts
        s._templates = icons + texts
    return dets


def pack_shared_detectors():
    """Language-neutral + JA death (death had 0 mismatches)."""
    cfg = load_config(default_config_path())
    cfg.vision.language = VisionLanguage.JA
    cfg.vision.enabled_detectors = [
        "timer",
        "death",
        "active_gameplay",
        "map_overlay",
    ]
    return cfg, {d.name: d for d in build_detectors(cfg.vision)}


def respawn_full_search(roi: np.ndarray, templates: list[NamedTpl]) -> list[dict]:
    if roi.size == 0 or not templates:
        return []
    gray = _to_gray(roi)
    white = _white_mask_bgr(roi)
    ih, iw = gray.shape[:2]
    results: list[dict] = []
    for tpl in templates:
        best_score = 0.0
        best_ch = None
        best_scale = None
        fitted = _fit_template_to_size(ih, iw, tpl.image)
        if fitted is None:
            results.append(
                {
                    "name": tpl.name,
                    "lang": tpl.lang,
                    "score": 0.0,
                    "channel": None,
                    "scale": None,
                }
            )
            continue
        for scale in _SCALES:
            if scale == 1.0:
                scaled = fitted
            else:
                new_w = max(4, int(fitted.shape[1] * scale))
                new_h = max(4, int(fitted.shape[0] * scale))
                if new_h > ih or new_w > iw:
                    continue
                scaled = cv2.resize(
                    fitted,
                    (new_w, new_h),
                    interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR,
                )
            for channel, search in (("gray", gray), ("white", white)):
                glyph = (
                    scaled
                    if channel == "gray"
                    else cv2.threshold(scaled, 180, 255, cv2.THRESH_BINARY)[1]
                )
                if glyph.shape[0] > ih or glyph.shape[1] > iw:
                    continue
                peak = float(
                    cv2.matchTemplate(search, glyph, cv2.TM_CCOEFF_NORMED).max()
                )
                if peak > best_score:
                    best_score = peak
                    best_ch = channel
                    best_scale = scale
        results.append(
            {
                "name": tpl.name,
                "lang": tpl.lang,
                "score": best_score,
                "channel": best_ch,
                "scale": best_scale,
            }
        )
    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def splat_named_best(gray: np.ndarray, templates: list[NamedTpl]) -> list[dict]:
    results: list[dict] = []
    for tpl in templates:
        best_score = 0.0
        best_scale = None
        for scale in _TEMPLATE_SCALES:
            tw = max(8, int(tpl.image.shape[1] * scale))
            th = max(8, int(tpl.image.shape[0] * scale))
            if th > gray.shape[0] or tw > gray.shape[1]:
                continue
            scaled = cv2.resize(tpl.image, (tw, th), interpolation=cv2.INTER_AREA)
            peak = float(cv2.matchTemplate(gray, scaled, cv2.TM_CCOEFF_NORMED).max())
            if peak > best_score:
                best_score = peak
                best_scale = scale
        results.append(
            {
                "name": tpl.name,
                "lang": tpl.lang,
                "score": best_score,
                "scale": best_scale,
                "kind": (
                    "text"
                    if ("splatted" in tpl.name or "taoshita" in tpl.name)
                    else "icon"
                ),
            }
        )
    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def event_key(e: GameEvent) -> tuple:
    return (
        e.event_type.value,
        round(e.start_time, 3),
        None if e.end_time is None else round(e.end_time, 3),
        e.source.value,
        None if e.reason is None else e.reason.value,
        e.from_lifecycle,
        e.to_lifecycle,
    )


def reading_summary(reading) -> dict:
    if reading is None:
        return {"detected": None}
    data = {"detected": bool(reading.detected), "kind": getattr(reading, "kind", None)}
    for field in (
        "evidence_type",
        "template_score",
        "confidence",
        "ocr_text",
        "countdown_value",
        "skull_score",
        "text_score",
    ):
        if hasattr(reading, field):
            data[field] = getattr(reading, field)
    if hasattr(reading, "instances"):
        data["n_instances"] = len(reading.instances)
        if reading.instances:
            data["fingerprints"] = [i.fingerprint for i in reading.instances]
    return data


def fuse_rows(rows: list[VisionFrameResult], cfg) -> tuple[list, list[GameEvent]]:
    snaps = fuse_game_state(
        rows,
        cfg.vision.timer,
        cfg.vision.state_fusion,
        cfg.vision.death,
        cfg.vision.splat,
        cfg.vision.respawn,
        cfg.vision.active_gameplay,
        cfg.vision.lifecycle,
        cfg.vision.map_overlay,
    )
    return snaps, infer_events(snaps, cfg.vision.events)


def main() -> int:
    log(f"frames={len(FRAMES)}")
    cfg, shared = pack_shared_detectors()
    dets_all = pack_lang_detectors("all")
    dets_ja = pack_lang_detectors("ja")
    log(
        f"templates ALL respawn={len(dets_all['respawn']._templates)} "
        f"splat={len(dets_all['splat']._templates)} | "
        f"JA respawn={len(dets_ja['respawn']._templates)} "
        f"splat={len(dets_ja['splat']._templates)}"
    )

    mismatches: list[dict] = []
    rows_all: list[VisionFrameResult] = []
    rows_ja: list[VisionFrameResult] = []

    for idx, path in enumerate(FRAMES):
        if idx % 100 == 0:
            log(f"scan {idx}/{len(FRAMES)} mismatches_so_far={len(mismatches)}")
        ts = float(path.stem.split("_")[1])
        img = cv2.imread(str(path))

        shared_dets = []
        for name, det in shared.items():
            reading, conf = det.detect(img, timestamp=ts)
            if reading is None:
                continue
            shared_dets.append(
                DetectorResult(
                    id=f"{name}:{idx}",
                    detector_name=name,
                    detector_version=f"{name}@p6",
                    confidence=conf,
                    reading=reading,
                )
            )

        # Language-varying detectors (detect for events; observe for mismatch list)
        vary_all = []
        vary_ja = []
        for name in ("respawn", "splat"):
            ra, ca = dets_all[name].detect(img, timestamp=ts)
            rj, cj = dets_ja[name].detect(img, timestamp=ts)
            if ra is not None:
                vary_all.append(
                    DetectorResult(
                        id=f"{name}:{idx}",
                        detector_name=name,
                        detector_version=f"{name}@p6",
                        confidence=ca,
                        reading=ra,
                    )
                )
            if rj is not None:
                vary_ja.append(
                    DetectorResult(
                        id=f"{name}:{idx}",
                        detector_name=name,
                        detector_version=f"{name}@p6",
                        confidence=cj,
                        reading=rj,
                    )
                )
            oa = dets_all[name]._observe(img)[0]
            oj = dets_ja[name]._observe(img)[0]
            if bool(oa.detected) != bool(oj.detected):
                mismatches.append(
                    {
                        "idx": idx,
                        "ts": ts,
                        "file": path.name,
                        "detector": name,
                        "all_observe": reading_summary(oa),
                        "ja_observe": reading_summary(oj),
                        "all_detect": reading_summary(ra),
                        "ja_detect": reading_summary(rj),
                    }
                )

        rows_all.append(
            VisionFrameResult(
                frame_id=f"f:{idx}",
                timestamp=ts,
                source="cadence",
                source_frame_index=idx,
                detections=shared_dets + vary_all,
            )
        )
        rows_ja.append(
            VisionFrameResult(
                frame_id=f"f:{idx}",
                timestamp=ts,
                source="cadence",
                source_frame_index=idx,
                detections=list(shared_dets) + vary_ja,
            )
        )

    log(f"FOUND {len(mismatches)} observe-mismatches")
    for m in mismatches:
        log(
            f"  {m['detector']}@{m['ts']:.1f} {m['file']} "
            f"ALL={m['all_observe']['detected']} JA={m['ja_observe']['detected']}"
        )

    resp_all = load_named_flat(CFG.vision.respawn.template_dir, ("en", "ja"))
    resp_ja_t = load_named_flat(CFG.vision.respawn.template_dir, ("ja",))
    splat_icons_all, splat_texts_all = load_named_splat(
        CFG.vision.splat.template_dir, ("en", "ja")
    )
    splat_icons_ja, splat_texts_ja = load_named_splat(
        CFG.vision.splat.template_dir, ("ja",)
    )

    details: list[dict] = []
    for m in mismatches:
        path = FRAMES[m["idx"]]
        img = cv2.imread(str(path))
        det = m["detector"]
        detail: dict = dict(m)
        if det == "respawn":
            roi = crop_roi(img, CFG.vision.respawn.roi)
            rank_all = respawn_full_search(roi, resp_all)
            rank_ja = respawn_full_search(roi, resp_ja_t)
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY).reshape(-1).astype(np.float32)
            dark = float((gray <= 40).mean())
            bright = float((gray >= 200).mean())
            p95 = float(np.percentile(gray, 95) / 255.0)
            structure_ok = (
                dark >= CFG.vision.respawn.dark_frac_min
                and bright >= CFG.vision.respawn.bright_frac_min
                and p95 >= CFG.vision.respawn.p95_min
            )
            win_all = rank_all[0] if rank_all else None
            win_ja = rank_ja[0] if rank_ja else None
            en_best = next((r for r in rank_all if r["lang"] == "en"), None)
            all_det = bool(m["all_observe"]["detected"])
            ja_det = bool(m["ja_observe"]["detected"])
            match_thr = CFG.vision.respawn.match_threshold
            support_thr = CFG.vision.respawn.support_match_threshold

            def primary(score: float) -> bool:
                return score >= match_thr and (
                    structure_ok or score >= _STRONG_TEMPLATE_FLOOR
                )

            def support(score: float) -> bool:
                return structure_ok and score >= support_thr

            if (
                all_det
                and not ja_det
                and en_best
                and primary(en_best["score"])
                and not primary(win_ja["score"] if win_ja else 0.0)
                and not support(win_ja["score"] if win_ja else 0.0)
            ):
                category = "A"
                cause = (
                    f"EN {en_best['name']}@{en_best['score']:.4f} "
                    f"{en_best['channel']}@{en_best['scale']} trips ALL; "
                    f"best JA {win_ja['name'] if win_ja else None}"
                    f"@{win_ja['score'] if win_ja else 0:.4f} does not"
                )
            elif win_all and win_ja and win_all["name"] == win_ja["name"] and all_det != ja_det:
                category = "C"
                cause = "same winner template; observe decision path differs"
            elif all_det != ja_det:
                category = "B"
                cause = (
                    f"JA ambiguity/ranking: ALL win={win_all} JA win={win_ja} "
                    f"EN best={en_best}"
                )
            else:
                category = "D"
                cause = "other"
            detail.update(
                {
                    "category": category,
                    "cause": cause,
                    "structure_ok": structure_ok,
                    "dark_frac": dark,
                    "bright_frac": bright,
                    "p95": p95,
                    "thresholds": {
                        "match": match_thr,
                        "support": support_thr,
                        "strong_floor": _STRONG_TEMPLATE_FLOOR,
                    },
                    "win_all": win_all,
                    "second_all": rank_all[1] if len(rank_all) > 1 else None,
                    "win_ja": win_ja,
                    "second_ja": rank_ja[1] if len(rank_ja) > 1 else None,
                    "en_best": en_best,
                    "rank_all_top5": rank_all[:5],
                    "rank_ja_top5": rank_ja[:5],
                }
            )
        else:
            roi = crop_roi(img, CFG.vision.splat.banner_roi)
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            icons_all = splat_named_best(gray, splat_icons_all)
            texts_all = splat_named_best(gray, splat_texts_all)
            icons_ja = splat_named_best(gray, splat_icons_ja)
            texts_ja = splat_named_best(gray, splat_texts_ja)
            skull_thr = CFG.vision.splat.skull_match_threshold
            text_thr = CFG.vision.splat.text_match_threshold

            def trips(c: dict | None) -> bool:
                if c is None:
                    return False
                if c["kind"] == "icon":
                    return c["score"] >= skull_thr
                return c["score"] >= text_thr

            en_cands = [c for c in icons_all + texts_all if c["lang"] == "en"]
            ja_cands = icons_ja + texts_ja
            best_en = max(en_cands, key=lambda c: c["score"], default=None)
            best_ja = max(ja_cands, key=lambda c: c["score"], default=None)
            all_det = bool(m["all_observe"]["detected"])
            ja_det = bool(m["ja_observe"]["detected"])
            ja_can = any(trips(c) for c in ja_cands)
            en_can = trips(best_en)
            if all_det and not ja_det and en_can and not ja_can:
                category = "A"
                cause = (
                    f"EN {best_en['name']}@{best_en['score']:.4f} "
                    f"scale={best_en['scale']} trips; JA pack cannot "
                    f"(best JA {best_ja})"
                )
            elif (not all_det) and ja_det:
                category = "B"
                cause = f"JA-only detects; ALL does not (NMS/path). best_ja={best_ja} best_en={best_en}"
            elif all_det != ja_det and ja_can:
                category = "B"
                cause = f"JA can trip in isolation but path differs. best_ja={best_ja} best_en={best_en}"
            else:
                category = "D"
                cause = f"other best_en={best_en} best_ja={best_ja}"
            detail.update(
                {
                    "category": category,
                    "cause": cause,
                    "thresholds": {"skull": skull_thr, "text": text_thr},
                    "win_all_icon": icons_all[0] if icons_all else None,
                    "second_all_icon": icons_all[1] if len(icons_all) > 1 else None,
                    "win_all_text": texts_all[0] if texts_all else None,
                    "win_ja_icon": icons_ja[0] if icons_ja else None,
                    "second_ja_icon": icons_ja[1] if len(icons_ja) > 1 else None,
                    "win_ja_text": texts_ja[0] if texts_ja else None,
                    "best_en": best_en,
                    "best_ja": best_ja,
                    "icons_all": icons_all,
                    "texts_all": texts_all,
                    "icons_ja": icons_ja,
                    "texts_ja": texts_ja,
                }
            )
        details.append(detail)
        log(f"DETAIL {det}@{m['ts']:.1f} cat={detail['category']} | {detail['cause']}")

    log("fusing events...")
    snaps_all, ev_all = fuse_rows(rows_all, cfg)
    snaps_ja, ev_ja = fuse_rows(rows_ja, cfg)
    log(f"events ALL={len(ev_all)} JA={len(ev_ja)}")

    ka = [event_key(e) for e in ev_all]
    kj = [event_key(e) for e in ev_ja]
    first_div = next((i for i in range(min(len(ka), len(kj))) if ka[i] != kj[i]), None)
    log(f"first_div={first_div}")
    if first_div is not None:
        for j in range(max(0, first_div - 2), min(len(ka), first_div + 4)):
            log(f"  ALL[{j}]={ka[j]}")
        for j in range(max(0, first_div - 2), min(len(kj), first_div + 4)):
            log(f"  JA [{j}]={kj[j]}")

    for detail in details:
        ts = detail["ts"]
        sa = next((s for s in snaps_all if abs(s.timestamp - ts) < 1e-9), None)
        sj = next((s for s in snaps_ja if abs(s.timestamp - ts) < 1e-9), None)
        detail["lifecycle_all"] = {
            "player_lifecycle": getattr(sa, "player_lifecycle", None),
            "match_phase": getattr(sa, "match_phase", None),
            "player_alive": getattr(sa, "player_alive", None),
        }
        detail["lifecycle_ja"] = {
            "player_lifecycle": getattr(sj, "player_lifecycle", None),
            "match_phase": getattr(sj, "match_phase", None),
            "player_alive": getattr(sj, "player_alive", None),
        }
        # countdown evidence on snapshot if present
        for label, snap in (("all", sa), ("ja", sj)):
            if snap is None:
                continue
            detail[f"lifecycle_{label}"]["splat_instances"] = len(
                getattr(snap, "splat_instances", []) or []
            )
        window = 10.0
        detail["events_near_all"] = [
            list(event_key(e)) for e in ev_all if abs(e.start_time - ts) <= window
        ]
        detail["events_near_ja"] = [
            list(event_key(e)) for e in ev_ja if abs(e.start_time - ts) <= window
        ]
        detail["lifecycle_differs"] = detail["lifecycle_all"] != detail["lifecycle_ja"]
        detail["nearby_events_differ"] = (
            detail["events_near_all"] != detail["events_near_ja"]
        )
        # causal note
        if detail["lifecycle_differs"] or detail["nearby_events_differ"]:
            detail["affects_events"] = True
        else:
            detail["affects_events"] = False

    only_all = [list(k) for k in ka if k not in set(kj)]
    only_ja = [list(k) for k in kj if k not in set(ka)]
    div_t = ev_all[first_div].start_time if first_div is not None else None
    before = [
        {"detector": d["detector"], "ts": d["ts"], "category": d["category"]}
        for d in details
        if div_t is None or d["ts"] <= div_t + 1.0
    ]

    report = {
        "fixture": "2026-09-07 09-17-30",
        "frames": len(FRAMES),
        "note": (
            "Shared timer/death(JA)/active/map; only respawn+splat vary. "
            "Death had 0 mismatches previously."
        ),
        "mismatch_count": len(details),
        "event_counts": {"all": len(ev_all), "ja": len(ev_ja)},
        "first_divergence_index": first_div,
        "first_divergence_time_all": div_t,
        "first_divergence_all": list(ka[first_div]) if first_div is not None else None,
        "first_divergence_ja": list(kj[first_div]) if first_div is not None else None,
        "mismatches_at_or_before_first_div": before,
        "only_in_all": only_all,
        "only_in_ja": only_ja,
        "mismatches": details,
    }
    OUT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    log(f"wrote {OUT}")
    log(f"only_in_all ({len(only_all)}): {only_all}")
    log(f"only_in_ja ({len(only_ja)}): {only_ja}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
