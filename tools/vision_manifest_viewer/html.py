"""Render a self-contained HTML vision-manifest diagnostic viewer."""

from __future__ import annotations

import html
import json
from pathlib import Path

from vision_manifest_viewer.model import ManifestView


def render_html(view: ManifestView) -> str:
    """Return a standalone HTML document for ``view``."""
    payload = view.model_dump(mode="json")
    data_json = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    title = html.escape(f"Vision Manifest Viewer — {view.summary.video_label}")
    return _HTML_TEMPLATE.replace("__TITLE__", title).replace("__DATA_JSON__", data_json)


def write_html(view: ManifestView, output_path: Path) -> Path:
    """Write the viewer HTML next to (or at) ``output_path``."""
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_html(view), encoding="utf-8")
    return output_path


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>__TITLE__</title>
<style>
:root {
  --bg:#14161a; --panel:#1c1f26; --line:#2e3440; --text:#e8eaed; --muted:#9aa0a6;
  --accent:#7aa2f7; --death:#f7768e; --countdown:#e0af68; --life:#9ece6a; --splat:#bb9af7;
  --active:#7dcfff; --ok:#9ece6a; --warn:#e0af68; --bad:#f7768e;
  --mono:"JetBrains Mono","SF Mono",Menlo,Consolas,monospace;
}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--text);font:13px/1.45 var(--mono)}
header,section{border-bottom:1px solid var(--line);padding:10px 16px}
h1{font-size:15px;margin:0 0 4px;font-weight:600}
h2{font-size:11px;margin:0 0 8px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
.meta{color:var(--muted);display:flex;gap:14px;flex-wrap:wrap;font-size:12px}
.match-info{margin-top:8px;display:flex;gap:16px;flex-wrap:wrap;align-items:baseline;font-size:14px}
.match-info .label{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em;margin-right:6px}
.match-info .value{color:var(--text);font-weight:600}
.match-info .status{font-size:11px;color:var(--muted)}
.match-info .status.ok{color:var(--ok)}
.match-info .status.warn{color:var(--warn)}
.warnings{margin-top:8px}
.warnings{margin-top:6px;display:grid;gap:2px;font-size:12px}
.warnings .ok{color:var(--ok)} .warnings .warn{color:var(--warn)} .warnings .info{color:var(--accent)}
.filters{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:end}
.filters label{display:grid;gap:4px;color:var(--muted);font-size:11px}
.filters select,.filters input[type=number],.filters input[type=text]{background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:4px;padding:6px 8px;font:inherit;min-width:110px}
.howto{color:var(--muted);font-size:11px;line-height:1.45;margin:0 0 8px}
.howto ol{margin:4px 0 8px;padding-left:18px}
.howto li{margin:2px 0}
.labels{margin:0 0 8px;padding:0;list-style:none;font-size:11px;color:var(--muted)}
.labels li{margin:2px 0}
.labels strong{color:var(--text)}
button{background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:4px;padding:6px 10px;font:inherit;cursor:pointer}
button:hover,button.active{border-color:var(--accent);color:var(--accent)}
#lifecycle-strip{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:10px;overflow-x:auto;overflow-y:visible}
.axis{position:relative;height:22px;border-bottom:1px solid var(--line);margin-bottom:8px;min-width:720px}
.tick{position:absolute;transform:translateX(-50%);color:var(--muted);font-size:11px}
.life-row{position:relative;min-width:720px;margin-top:8px}
.splat-row{position:relative;min-width:720px;height:28px;margin-top:8px;border-top:1px dashed var(--line);padding-top:6px}
.splat-row .lane-name{position:absolute;left:0;top:8px;font-size:10px;color:var(--splat);letter-spacing:.06em;text-transform:uppercase}
.splat-badge{position:absolute;transform:translateX(-50%);top:4px;min-width:44px;padding:2px 6px;border-radius:10px;background:var(--splat);color:#1a1224;font-size:9px;font-weight:700;letter-spacing:.04em;text-align:center;cursor:pointer;border:1px solid #000;z-index:2}
.splat-badge:hover,.splat-badge.selected{outline:2px solid #fff;outline-offset:1px}
.splat-tick{position:absolute;transform:translateX(-50%);top:10px;width:8px;height:8px;border-radius:50%;background:#3b3358;border:1px solid var(--splat);cursor:pointer;z-index:1}
.splat-tick:hover,.splat-tick.selected{background:var(--splat);outline:2px solid #fff;outline-offset:1px}
.metric-row{position:relative;min-width:720px;height:36px;margin-top:8px;border-top:1px dashed var(--line);padding-top:6px}
.metric-row .lane-name{position:absolute;left:0;top:10px;font-size:10px;letter-spacing:.06em;text-transform:uppercase}
.metric-row.map-ink .lane-name{color:#7dcfff}
.metric-row.special .lane-name{color:#e0af68}
.map-ink-tick{position:absolute;transform:translateX(-50%);top:12px;width:7px;height:7px;border-radius:50%;background:#1a3040;border:1px solid #7dcfff;cursor:pointer;z-index:1}
.map-ink-tick:hover,.map-ink-tick.selected{background:#7dcfff;outline:2px solid #fff;outline-offset:1px}
.special-tick{position:absolute;transform:translateX(-50%);top:14px;width:6px;height:6px;border-radius:50%;background:#3b3358;border:1px solid #e0af68;cursor:pointer;z-index:1}
.special-tick .lbl{position:absolute;top:-14px;left:50%;transform:translateX(-50%);font-size:9px;color:#e0af68;white-space:nowrap;pointer-events:none}
.special-tick.ready{top:10px;width:12px;height:12px;background:#e0af68;border:2px solid #fff;box-shadow:0 0 0 1px #000}
.special-tick.ready .lbl{top:-16px;color:#ffd9a0;font-weight:700}
.special-tick:hover,.special-tick.selected{outline:2px solid #fff;outline-offset:1px}
.side-metrics{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:10px 0}
.side-metrics .panel{background:#0b0d10;border:1px solid var(--line);border-radius:6px;padding:8px}
.side-metrics h3{margin:0 0 6px;font-size:11px;color:var(--muted);text-transform:uppercase}
.life-seg{position:absolute;top:8px;height:14px;border-radius:3px;opacity:.9;min-width:2px}
.life-seg.unknown{background:#565f89}.life-seg.alive{background:var(--life)}.life-seg.dead{background:var(--death)}
.life-seg.countdown{background:var(--countdown)}.life-seg.respawned{background:#73daca}.life-seg.awaiting_control{background:#7aa2f7}
.life-mark{position:absolute;transform:translateX(-50%);text-align:center;cursor:pointer;z-index:1;max-width:110px}
.life-mark:hover,.life-mark.selected{z-index:5}
.life-mark .dot{width:10px;height:10px;border-radius:50%;background:var(--accent);margin:0 auto;border:1px solid #000}
.life-mark.dead .dot{background:var(--death)}
.life-mark.countdown .dot{background:var(--countdown)}
.life-mark.respawned .dot{background:#73daca}
.life-mark.awaiting-control .dot{background:#7aa2f7}
.life-mark.alive .dot{background:var(--life)}
.life-mark.splat .dot{background:var(--splat)}
.life-mark.selected .dot{outline:2px solid #fff;outline-offset:2px}
.life-mark .stem{width:1px;height:8px;background:var(--line);margin:0 auto}
.life-mark .lbl{font-size:10px;line-height:1.2;padding:2px 4px;border-radius:3px;background:rgba(20,22,26,.92);border:1px solid var(--line);white-space:normal;word-break:break-word}
.life-mark .t{color:var(--muted)}.life-mark .e{color:var(--text)}
.life-mark.compact .e{font-size:9px}
.gallery{display:flex;gap:10px;overflow-x:auto;padding-bottom:6px}
.card{flex:0 0 176px;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:8px;cursor:pointer;box-shadow:none;outline:none}
.card.no-thumb{flex-basis:148px}
.card.flag-dpen{border-color:var(--warn)}
.card.flag-fp{border-color:var(--bad)}
.card.flag-miss{border-color:var(--warn)}
.card.selected{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent);outline:2px solid #fff;outline-offset:1px;z-index:2}
.card img{width:100%;height:72px;object-fit:cover;background:#0b0d10;border:1px dashed var(--line);border-radius:4px;display:block}
.card .ph,.frame-stage .ph{min-height:72px;display:grid;place-items:center;color:var(--muted);border:1px dashed var(--line);border-radius:4px;padding:8px;text-align:center;font-size:10px;line-height:1.35}
.ph[hidden]{display:none !important}
.card.no-thumb .ph{min-height:40px;padding:4px 2px;font-size:9px}
.frame-stage .ph{min-height:120px;font-size:11px}
.card .when{display:block;color:var(--muted);margin-top:4px;font-size:11px}
.chips{display:grid;grid-template-columns:1fr 1fr;gap:3px;margin-top:6px}
.chip{font-size:9px;line-height:1.3;padding:2px 4px;border-radius:3px;border:1px solid var(--line);color:var(--muted);background:#0b0d10;cursor:pointer}
button.chip{padding:2px 4px;font-size:9px}
.chip.on{color:var(--text);border-color:var(--accent)}
.chip.picked{outline:1px solid #fff}
.chip .gtb{display:block;font-size:8px;letter-spacing:.04em;margin-top:1px;color:var(--muted)}
.chip .gtb.hit,.chip .gtb.reject{color:var(--ok)}
.chip .gtb.miss{color:var(--warn)}
.chip .gtb.fp,.chip .gtb.mismatch{color:var(--bad)}
.chip.splat-chip.on{border-color:var(--splat);color:#e0d4ff}
.nav-row{display:flex;gap:8px;margin-bottom:8px;align-items:center;flex-wrap:wrap}
#gallery-window{color:var(--muted);font-size:11px}
.detail{display:grid;grid-template-columns:minmax(360px,1.6fr) minmax(280px,1fr) minmax(320px,1.3fr);gap:16px}
.frame-preview{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:8px}
.frame-stage{position:relative}
.frame-preview img{width:100%;max-height:450px;object-fit:contain;display:block;background:#0b0d10;border-radius:4px}
.roi-box{position:absolute;border:2px solid var(--countdown);pointer-events:none;box-shadow:0 0 0 1px #000}
.layers{display:grid;grid-template-columns:1fr;gap:8px;margin:10px 0}
.layer{background:#0b0d10;border:1px solid var(--line);border-radius:6px;padding:8px}
.layer h3{margin:0 0 6px;font-size:11px;color:var(--muted);text-transform:uppercase}
.kv{display:grid;grid-template-columns:1fr auto;gap:3px 10px}
.kv span:nth-child(odd){color:var(--muted)}
.causal,.diag-panel,.gt-panel,.stats-panel{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:10px;margin-top:10px}
.causal.bad{border-color:var(--bad)}.causal.warn{border-color:var(--warn)}.causal.ok{border-color:var(--ok)}
.causal h3,.diag-panel h3,.gt-panel h3{margin:0 0 8px;font-size:13px}
.verdict{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:10px;text-align:center}
.verdict .v{font-size:15px;font-weight:700;letter-spacing:.04em}
.verdict .sub{display:block;color:var(--muted);font-size:11px;margin-top:4px;line-height:1.4}
.verdict.hit{border-color:var(--ok)}.verdict.hit .v{color:var(--ok)}
.verdict.fp,.verdict.mismatch{border-color:var(--bad)}.verdict.fp .v,.verdict.mismatch .v{color:var(--bad)}
.verdict.miss{border-color:var(--warn)}.verdict.miss .v{color:var(--warn)}
.verdict.reject{border-color:var(--ok)}.verdict.reject .v{color:var(--muted)}
.verdict.unmarked .v{color:var(--muted)}
.chain-step{padding:8px 10px;border-left:3px solid var(--line);margin:0 0 6px 4px;background:#0b0d10;border-radius:0 6px 6px 0}
.chain-step.ok{border-left-color:var(--ok)}.chain-step.bad{border-left-color:var(--bad)}
.chain-step .arrow{color:var(--muted);text-align:center;margin:2px 0;font-size:11px}
.cue-row{display:grid;grid-template-columns:180px 1fr 52px 28px;gap:8px;align-items:center;margin:4px 0}
.bar{height:10px;background:#0b0d10;border-radius:3px;overflow:hidden;border:1px solid var(--line)}
.bar > i{display:block;height:100%;background:var(--accent)}
.bar > i.pass{background:var(--ok)}.bar > i.fail{background:var(--bad)}
.decision{margin-top:10px;padding:10px;border-radius:6px;background:#0b0d10;text-align:center;font-size:14px;font-weight:700}
.decision.confirmed{color:var(--ok);border:1px solid var(--ok)}
.decision.rejected,.decision.suppressed{color:var(--bad);border:1px solid var(--bad)}
.decision .sub{display:block;font-weight:400;font-size:11px;color:var(--muted);margin-top:4px}
.check{margin:2px 0}.check.ok{color:var(--ok)}.check.bad{color:var(--bad)}
.meaning{color:var(--muted);margin-top:8px}
.actions{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
pre.raw{background:#0b0d10;border:1px solid var(--line);border-radius:6px;padding:10px;overflow:auto;max-height:260px;display:none}
pre.raw.open{display:block}
.roi-panel{display:none;margin-top:10px}.roi-panel.open{display:block}
.roi-panel canvas{width:100%;background:#0b0d10;border-radius:4px}
.episodes{display:grid;gap:12px}
.episode{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:10px 12px}
.episode h3{margin:0 0 8px;font-size:13px}
.scenario-card .meta{margin:0 0 8px}
.scenario-blocks{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px}
.scenario-card.reviewing{border-color:var(--accent)}
.review-layout{display:grid;grid-template-columns:minmax(320px,1.3fr) minmax(280px,1fr);gap:16px}
.review-player{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:8px}
.review-player video{width:100%;max-height:420px;background:#0b0d10;border-radius:4px;display:block}
.review-events{margin:8px 0 0;padding:0;list-style:none}
.review-events li{display:grid;grid-template-columns:70px 1fr;gap:8px;padding:2px 0;border-bottom:1px solid var(--line)}
.review-events .t{color:var(--muted)}
@media (max-width:900px){.review-layout{grid-template-columns:1fr}}
.episode.incomplete{border-color:var(--warn)}
.comp{display:flex;gap:12px;flex-wrap:wrap;margin:6px 0 10px}
.comp .c{font-size:11px}.comp .c.ok{color:var(--ok)}.comp .c.bad{color:var(--bad)}
.latch{margin:8px 0;padding:8px;background:#0b0d10;border-radius:6px}
.latch-row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.latch-item{text-align:center;min-width:70px}
.latch-item .v{font-weight:700}.latch-item .v.t{color:var(--ok)}.latch-item .v.f{color:var(--muted)}
.step{display:grid;grid-template-columns:70px 130px 1fr;gap:8px;padding:3px 0;border-left:2px solid var(--line);padding-left:10px;margin-left:4px;cursor:pointer}
.step:hover{border-left-color:var(--accent)}
.gt-radios{display:flex;flex-wrap:wrap;gap:8px 14px;margin:8px 0}
.gt-radios label{display:flex;gap:6px;align-items:center;font-size:12px}
.gt-list{margin-top:8px;display:grid;gap:4px}
.gt-item{display:flex;justify-content:space-between;gap:8px;padding:4px 0;border-bottom:1px solid var(--line)}
.stats-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.stats-grid div{background:#0b0d10;border-radius:6px;padding:8px;text-align:center}
.stats-grid strong{display:block;font-size:18px}
.stats-grid span{color:var(--muted);font-size:11px}
@media (max-width:1200px){.detail{grid-template-columns:1fr 1fr}}
@media (max-width:900px){.detail,.layers,.stats-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <h1>VISION MANIFEST VIEWER</h1>
  <div class="match-info" id="match-info"></div>
  <div class="meta" id="header-meta"></div>
  <div class="warnings" id="warnings"></div>
</header>

<section>
  <h2>Filters</h2>
  <div class="filters">
    <label>Time start<input id="filter-t0" type="text" inputmode="decimal" value="0" placeholder="0 or 0:00" title="Seconds or mm:ss"/></label>
    <label>Time end<input id="filter-t1" type="text" inputmode="decimal" value="0" placeholder="mm:ss or seconds" title="Seconds or mm:ss"/></label>
    <label>Detector<select id="filter-detector"></select></label>
    <label>Confidence ≥<input id="filter-conf" type="number" min="0" max="1" step="0.05" value="0"/></label>
  </div>
</section>

<section>
  <h2>Lifecycle</h2>
  <div id="lifecycle-strip"></div>
</section>

<section>
  <h2>Cadence frames</h2>
  <div class="nav-row">
    <button type="button" id="btn-gal-earlier">« Earlier</button>
    <button type="button" id="btn-prev">← Previous</button>
    <button type="button" id="btn-next">Next →</button>
    <button type="button" id="btn-gal-later">Later »</button>
    <span id="gallery-window"></span>
  </div>
  <div class="gallery" id="gallery"></div>
</section>

<section>
  <h2>Selected observation</h2>
  <div class="detail">
    <div>
      <div class="frame-preview"><div class="frame-stage" id="frame-stage">
        <div class="ph" id="frame-ph">No saved image</div>
        <img id="frame-img" alt="frame" hidden/>
        <div class="roi-box" id="roi-overlay" hidden></div>
      </div></div>
      <div class="actions">
        <button type="button" id="btn-roi">View ROI</button>
        <button type="button" id="btn-raw">Raw JSON</button>
      </div>
      <div class="roi-panel" id="roi-panel">
        <h2>ROI</h2>
        <canvas id="roi-canvas" width="480" height="120"></canvas>
        <div class="kv" id="roi-kv" style="margin-top:8px"></div>
      </div>
    </div>
    <div>
      <div class="verdict" id="gt-verdict"></div>
      <div class="gt-panel" id="gt-panel">
        <h3>Ground truth</h3>
        <div class="howto" id="gt-howto">Each mark applies to the selected cadence frame only. <code>real_death</code> is the DEATH event (first alive→dead), not every Ouch / DeathReading. <code>real_splat</code> is the SPLAT event (first rising edge), not every kill-banner frame. Later frames in the same episode stay unmarked. Exporting does not retrain detectors.</div>
        <ul class="labels" id="gt-cheat"></ul>
        <div class="gt-radios" id="gt-radios"></div>
        <div class="filters" style="margin-top:8px">
          <label>GT channel<select id="gt-channel">
            <option value="evidence">Detector evidence</option>
            <option value="match_phase">Match state</option>
          </select></label>
          <button type="button" id="btn-gt-export">Export GT JSON</button>
          <button type="button" id="btn-gt-clear">Clear all GT</button>
        </div>
        <div class="gt-list" id="gt-list"></div>
      </div>
      <div class="stats-panel" id="stats-panel">
        <h3 id="stats-title">Death performance (GT)</h3>
        <div class="stats-grid" id="stats-grid"></div>
      </div>
    </div>
    <div>
      <div class="kv" id="obs-meta"></div>
      <div class="layers" id="layers"></div>
      <div class="causal" id="causal" hidden></div>
      <div class="diag-panel" id="diag-panel" hidden></div>
      <div id="reading"></div>
      <pre class="raw" id="raw-json"></pre>
    </div>
  </div>
</section>

<section id="scenario-review" hidden>
  <h2>Scenario Review</h2>
  <p class="howto">Scenario = ownership (<code>event_ids</code>): what belongs here. ScenarioContext = measured evidence on the card: timeline, map, splat observations, death episode, and relations. Coaching = interpretation; this UI does not show judgments. ENGAGEMENT is a splat observation cluster, not necessarily a complete fight. Relations are temporal associations under configured rules, not causal claims. <code>trade_candidate</code> is a temporal window flag only. An associated/following death does not prove the splat caused the death. Scenario outcomes are not fight quality.</p>
  <div class="review-layout">
    <div class="review-player">
      <video id="review-video" controls playsinline preload="metadata"></video>
      <p class="howto" id="review-video-note"></p>
      <div class="nav-row">
        <button type="button" id="btn-review-prev">Previous scenario</button>
        <button type="button" id="btn-review-next">Next scenario</button>
      </div>
    </div>
    <div id="review-detail"></div>
  </div>
</section>

<section id="scenario-evidence">
  <h2>Scenarios / Coaching Evidence</h2>
  <p class="howto">Scenario = ownership (<code>event_ids</code>): what belongs here. ScenarioContext = measured evidence on the card: timeline, map, splat observations, death episode, and relations. Coaching = interpretation; this UI does not show judgments. ENGAGEMENT is a splat observation cluster, not necessarily a complete fight. Relations are temporal associations under configured rules, not causal claims. <code>trade_candidate</code> is a temporal window flag only. An associated/following death does not prove the splat caused the death. Scenario outcomes are not fight quality.</p>
  <div class="episodes" id="scenario-cards"></div>
</section>

<script id="viewer-data" type="application/json">__DATA_JSON__</script>
<script>
const DATA = JSON.parse(document.getElementById("viewer-data").textContent);
const GT_KEY = "vmv-gt:" + (DATA.manifest_path || DATA.summary.video_identity || "default");
const state = {
  selectedId:null, selectedTransitionId:null, showRoi:false, showRaw:false, filtered:[],
  gt: loadGt(),
  galleryOffset: 0,  // index into grouped cadence tiles for gallery window start
  reviewIndex: 0,
};
const GALLERY_PAGE = 200;
const REVIEW_VIDEO_ROUTE = "/review-video";
const $ = id => document.getElementById(id);
const esc = s => String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
const num = v => (v==null||Number.isNaN(Number(v))) ? "—" : Number(v).toFixed(3);

/** Compact mm:ss (optional fractional seconds). */
function fmtMmSs(s, decimals=0) {
  if (s==null || Number.isNaN(Number(s))) return "—";
  const sec = Math.max(0, Number(s));
  const m = Math.floor(sec / 60);
  const rem = sec - m * 60;
  if (decimals <= 0) {
    return `${String(m).padStart(2,"0")}:${String(Math.floor(rem + 1e-9)).padStart(2,"0")}`;
  }
  const fixed = rem.toFixed(decimals);
  const [a, b] = fixed.split(".");
  return `${String(m).padStart(2,"0")}:${a.padStart(2,"0")}.${b}`;
}

/** Dual display: mm:ss + raw seconds, e.g. "03:09.0 (189.0s)". */
function fmtTime(s, decimals=1) {
  if (s==null || Number.isNaN(Number(s))) return "—";
  const sec = Number(s);
  return `${fmtMmSs(sec, decimals)} (${sec.toFixed(decimals)}s)`;
}

/** Parse seconds or mm:ss (also "3:09 (189s)" and trailing "s"). */
function parseTime(text) {
  if (text==null || text==="") return NaN;
  const cleaned = String(text).trim().replace(/,/g, "").replace(/\s*s$/i, "");
  const dual = cleaned.match(/^(\d+):(\d+(?:\.\d+)?)\s*\(/);
  if (dual) return Number(dual[1]) * 60 + Number(dual[2]);
  const m = cleaned.match(/^(\d+):(\d+(?:\.\d+)?)$/);
  if (m) return Number(m[1]) * 60 + Number(m[2]);
  return Number(cleaned);
}

function filterTimeRange() {
  const t0 = parseTime($("filter-t0").value || "0");
  const t1 = parseTime($("filter-t1").value || "1e9");
  return { t0: Number.isNaN(t0) ? 0 : t0, t1: Number.isNaN(t1) ? 1e9 : t1 };
}
const GT_LABELS = {
  unknown:"Unknown",
  real_death:"Real death", not_a_death:"Not a death",
  damage_recovery:"Damage/recovery", results_screen:"Results screen", lobby:"Lobby",
  real_splat:"Real splat", not_a_splat:"Not a splat",
  real_active_gameplay:"Real active gameplay", not_active_gameplay:"Not active gameplay",
  real_map_overlay:"Real map overlay", not_a_map_overlay:"Not an overlay",
  real_respawn:"Real respawn", not_a_respawn:"Not a respawn",
  real_timer:"Real timer", not_a_timer:"Not a timer",
  intro:"Intro", opening_countdown:"Opening countdown", in_match:"In match",
  post_match:"Post match", results_lobby:"Results / lobby",
  death_state:"Death state", respawn_state:"Respawn state",
};
const GT_BY_DETECTOR = {
  death:["unknown","real_death","not_a_death","damage_recovery"],
  splat:["unknown","real_splat","not_a_splat"],
  active_gameplay:["unknown","real_active_gameplay","not_active_gameplay"],
  map_overlay:["unknown","real_map_overlay","not_a_map_overlay"],
  respawn:["unknown","real_respawn","not_a_respawn"],
  timer:["unknown","real_timer","not_a_timer"],
  match_phase:["unknown","intro","opening_countdown","in_match","post_match","results_lobby"],
};
const GT_LABEL_MEANINGS = {
  real_death:"DEATH event: first alive→dead transition in the episode. DeathReading.detected ≠ DEATH. Do not mark later Ouch frames in the same cycle (leave them unmarked, not not_a_death).",
  not_a_death:"Death detector false positive — Ouch UI when this was not a death episode.",
  damage_recovery:"Death-like HUD without a death.",
  real_splat:"SPLAT event: first rising edge of a local kill. SplatReading.detected ≠ SPLAT. Do not mark later banner frames in the same kill (leave them unmarked, not not_a_splat).",
  not_a_splat:"Splat detector false positive — kill banner when you did not splat someone.",
  real_active_gameplay:"Fused ACTIVE state (in-match and alive), not HUD chrome.",
  not_active_gameplay:"Not the ACTIVE state (intro, death-cam, countdown, …).",
  real_respawn:"RESPAWN event — first enter respawned in the episode.",
  not_a_respawn:"Respawn detector false positive.",
  real_map_overlay:"The map was open.",
  not_a_map_overlay:"Map detector false positive.",
  real_timer:"Match timer was readable.",
  not_a_timer:"Timer detector false positive.",
};
const GT_CHEAT = {
  death:[
    ["Real death", GT_LABEL_MEANINGS.real_death],
    ["Not a death", GT_LABEL_MEANINGS.not_a_death],
    ["Damage/recovery", GT_LABEL_MEANINGS.damage_recovery],
  ],
  splat:[["Real splat",GT_LABEL_MEANINGS.real_splat],["Not a splat",GT_LABEL_MEANINGS.not_a_splat]],
  active_gameplay:[["Real active gameplay","ACTIVE state — you had control"],["Not active gameplay","not the ACTIVE state (intro, death-cam, …)"]],
  map_overlay:[["Real map overlay","the map was open"],["Not an overlay","pipeline was wrong"]],
  respawn:[["Real respawn","RESPAWN event / landing"],["Not a respawn","respawn detector false positive"]],
  timer:[["Real timer","match timer was readable"],["Not a timer","pipeline was wrong"]],
  match_phase:[
    ["Intro","pre-match, no ticking clock"],
    ["Opening countdown","frozen 5:00 / 3:00 before GO"],
    ["In match","clock has started"],
    ["Post match / results","lobby or results"],
  ],
};
const DETECTOR_TITLE = {
  death:"Death", splat:"Splat", active_gameplay:"Active gameplay",
  map_overlay:"Map overlay", respawn:"Respawn", timer:"Timer",
  match_phase:"Match state", special_gauge:"Special gauge",
};
const GT_CHIP_DETECTORS = ["timer","death","splat","respawn","active_gameplay","map_overlay"];

function loadGt(){
  try {
    const raw = JSON.parse(localStorage.getItem(GT_KEY)||"[]");
    return (Array.isArray(raw)?raw:[]).map(g => ({...g, detector: g.detector || "death"}));
  } catch { return []; }
}
function saveGt(){ localStorage.setItem(GT_KEY, JSON.stringify(state.gt)); renderGt(); renderStats(); refresh(); }

function init() {
  ensureCanonicalObservations();
  const detectors = [...new Set([...(DATA.detectors||[]), ...GT_CHIP_DETECTORS])];
  $("filter-detector").innerHTML = '<option value="">All</option>'+detectors.map(d=>`<option value="${esc(d)}">${esc(d)}</option>`).join("");
  $("filter-t1").value = DATA.summary.duration_seconds.toFixed(1);
  const filterIds = ["filter-detector","filter-conf","filter-t0","filter-t1"];
  for (const id of filterIds) {
    $(id).addEventListener("input", refresh); $(id).addEventListener("change", refresh);
  }
  $("btn-prev").onclick = () => step(-1);
  $("btn-next").onclick = () => step(1);
  $("btn-gal-earlier").onclick = () => shiftGallery(-GALLERY_PAGE);
  $("btn-gal-later").onclick = () => shiftGallery(GALLERY_PAGE);
  $("btn-roi").onclick = () => { state.showRoi=!state.showRoi; renderDetail(); };
  $("btn-raw").onclick = () => { state.showRaw=!state.showRaw; renderDetail(); };
  $("gt-channel").addEventListener("change", () => renderGtContext(selectedObs()));
  bindReviewControls();
  $("btn-gt-export").onclick = exportGt;
  $("btn-gt-clear").onclick = () => { if(confirm("Clear all ground-truth marks for this manifest?")){ state.gt=[]; saveGt(); } };
  refresh();
  renderGt();
  renderStats();
}

function diagFor(o){
  if (!o) return null;
  if (o.diagnostic_id) return (DATA.death_diagnostics||[]).find(d=>d.id===o.diagnostic_id)||null;
  return (DATA.death_diagnostics||[]).find(d=>d.observation_id===o.id)||null;
}

function gtDetector(g){ return g.detector || "death"; }

function reviewDetector(){
  const det=$("filter-detector").value;
  if (det) return det;
  return selectedObs()?.detector || "death";
}

function gtLabelsFor(detector){
  if (GT_BY_DETECTOR[detector]) return GT_BY_DETECTOR[detector];
  return ["unknown", "real_"+detector, "not_a_"+detector];
}

function isRealLabel(label, detector){
  if (detector==="death") return label==="real_death";
  return String(label||"").startsWith("real_");
}

function isNegativeLabel(label, detector){
  if (detector==="death") return ["not_a_death","damage_recovery","results_screen","lobby"].includes(label);
  return String(label||"").startsWith("not_");
}

function gtAt(ts, detector){
  const det = detector || reviewDetector();
  const hits = state.gt.filter(g => gtDetector(g)===det && ts >= g.t0 && ts <= g.t1);
  return hits.length ? hits[hits.length-1] : null;
}

/** What a GT label is actually judging for this detector.
 *
 * real_active_gameplay validates the fused ACTIVE state, not HUD evidence.
 * real_death validates the DEATH event (first alive→dead), not DeathReading.
 * real_splat validates the SPLAT event (first rising edge), not SplatReading.
 * not_a_death / not_a_splat still judge the detector (true false positive).
 */
function eventNear(ts, label){
  const want = String(label||"").toUpperCase();
  const events = (DATA.markers||[]).filter(m =>
    String(m.id||"").startsWith("event:") && String(m.label||"").toUpperCase()===want
  );
  return events.some(m => Math.abs(m.timestamp - ts) <= 0.26);
}

function deathEventNear(ts){ return eventNear(ts, "DEATH"); }
function splatEventNear(ts){ return eventNear(ts, "SPLAT"); }

function gtSubjectPositive(o, detector){
  const det = detector || o.detector;
  if (det === "active_gameplay") return o.active_gameplay === true;
  return !!o.positive;
}

function gtRealPositive(o, detector){
  const det = detector || o.detector;
  if (det === "death") return deathEventNear(o.timestamp);
  if (det === "splat") return splatEventNear(o.timestamp);
  return gtSubjectPositive(o, det);
}

function isFalsePositiveObs(o){
  if (!gtSubjectPositive(o)) return false;
  const g = gtAt(o.timestamp, o.detector);
  if (!g) return false;
  return isNegativeLabel(g.label, o.detector);
}

function isMissedObs(o){
  const g = gtAt(o.timestamp, o.detector);
  if (!g || !isRealLabel(g.label, o.detector)) return false;
  return !gtRealPositive(o, o.detector);
}

/** Expected match_phase values for a match-state GT label. */
function matchPhaseExpected(label){
  return label === "results_lobby" ? ["post_match","out_of_match"] : [label];
}

/** Inferred GT verdict for one observation, or null when nothing applies. */
function gtVerdict(o){
  if (!o) return null;
  const det = gtChannelDetector(o);
  const g = gtAt(o.timestamp, det);
  if (!g) return {kind:"unmarked", title:"UNMARKED", detail:"No ground truth covers this frame."};
  const shown = GT_LABELS[g.label] || g.label;
  if (det === "match_phase") {
    const want = matchPhaseExpected(g.label);
    const got = o.match_phase || "—";
    return want.includes(got)
      ? {kind:"hit", title:"MATCH", detail:`match_phase is ${got}, GT says ${shown}.`}
      : {kind:"mismatch", title:"MISMATCH", detail:`match_phase is ${got}, GT says ${shown}.`};
  }
  const real = isRealLabel(g.label, det);
  const fired = real ? gtRealPositive(o, det) : gtSubjectPositive(o, det);
  const subject = det === "active_gameplay" ? "fused active_gameplay"
    : (det === "death" && real) ? "DEATH event"
    : (det === "splat" && real) ? "SPLAT event"
    : `${det} detector`;
  if (real) {
    return fired
      ? {kind:"hit", title:"HIT", detail:`${subject} fired and GT says ${shown}.`}
      : {kind:"miss", title:"MISS", detail:`GT says ${shown} but ${subject} did not fire.`};
  }
  if (isNegativeLabel(g.label, det)) {
    return fired
      ? {kind:"fp", title:"FALSE POSITIVE", detail:`${subject} fired but GT says ${shown}.`}
      : {kind:"reject", title:"CORRECT REJECT", detail:`${subject} stayed quiet and GT says ${shown}.`};
  }
  return {kind:"unmarked", title:"MARKED", detail:`GT says ${shown}.`};
}

function renderGtVerdict(o){
  const box=$("gt-verdict");
  const v=gtVerdict(o);
  if (!v){ box.className="verdict unmarked"; box.innerHTML=`<span class="v">—</span><span class="sub">Select a cadence tile.</span>`; return; }
  box.className="verdict "+v.kind;
  box.innerHTML=`<span class="v">${esc(v.title)}</span><span class="sub">${esc(v.detail)}</span>`;
}

function inTimeRange(o) {
  const {t0,t1}=filterTimeRange();
  return o.timestamp>=t0 && o.timestamp<=t1;
}

function frameKey(o) {
  return o.frame_id || ("ts:" + o.timestamp);
}

function ensureCanonicalObservations() {
  const byFrame = new Map();
  for (const o of DATA.observations || []) {
    const k = frameKey(o);
    if (!byFrame.has(k)) byFrame.set(k, { proto: o, dets: new Set() });
    if (o.detector) byFrame.get(k).dets.add(o.detector);
  }
  for (const [k, { proto, dets }] of byFrame) {
    for (const det of GT_CHIP_DETECTORS) {
      if (dets.has(det)) continue;
      DATA.observations.push({
        id: `absent:${k}:${det}`,
        timestamp: proto.timestamp,
        frame_index: proto.frame_index,
        frame_id: proto.frame_id,
        frame_path: proto.frame_path,
        image_relpath: proto.image_relpath,
        detector: det,
        category: "other",
        confidence: null,
        positive: false,
        reading: {},
        lifecycle: proto.lifecycle,
        player_alive: proto.player_alive,
        countdown_present: proto.countdown_present,
        active_gameplay: proto.active_gameplay,
        match_phase: proto.match_phase,
        latch: proto.latch,
        source: proto.source,
        layer: "observation",
        diagnostic_flags: [],
      });
    }
  }
}

function chipReadings(tile) {
  return GT_CHIP_DETECTORS.map(det => tile.readings.find(o => o.detector===det)).filter(Boolean);
}

function groupTiles(obs) {
  const map = new Map();
  for (const o of obs) {
    const k = frameKey(o);
    if (!map.has(k)) {
      map.set(k, {
        key: k,
        timestamp: o.timestamp,
        frame_id: o.frame_id,
        image_relpath: o.image_relpath,
        source: o.source,
        readings: [],
      });
    }
    const tile = map.get(k);
    if (!tile.image_relpath && o.image_relpath) tile.image_relpath = o.image_relpath;
    tile.readings.push(o);
  }
  return Array.from(map.values()).sort((a,b)=>a.timestamp-b.timestamp || String(a.key).localeCompare(String(b.key)));
}

function tilePasses(tile) {
  const det=$("filter-detector").value;
  const conf=parseFloat($("filter-conf").value||"0");
  if (det) {
    const r = tile.readings.find(o => o.detector===det);
    if (!r || !r.positive) return false;
    if (conf > 0 && (r.confidence==null || r.confidence<conf)) return false;
  } else if (conf > 0 && !tile.readings.some(o => o.positive && o.confidence!=null && o.confidence>=conf)) {
    return false;
  }
  return true;
}

function filteredTiles() {
  const obs = DATA.observations.filter(o => o.detector && inTimeRange(o));
  return groupTiles(obs).filter(tilePasses);
}

/** Rank a thumbnail's readings: filtered detector, else any positive, timer last. */
function preferredReading(tile) {
  if (!tile || !tile.readings.length) return null;
  const det=$("filter-detector").value;
  if (det) {
    const hit = tile.readings.find(o => o.detector===det);
    if (hit) return hit;
  }
  const rank = (o) => {
    const positive = o.positive ? 200 : 0;
    const timerPenalty = o.detector === "timer" ? 0 : 50;
    return positive + timerPenalty + (o.confidence || 0);
  };
  return tile.readings.slice().sort((a, b) => rank(b) - rank(a))[0];
}

function primaryReading(tile) {
  if (!tile || !tile.readings.length) return null;
  const selected = tile.readings.find(o => o.id===state.selectedId);
  if (selected) return selected;
  return preferredReading(tile);
}

function isTileSelected(tile) {
  const o = selectedObs();
  return !!o && tile.key === frameKey(o);
}

function refresh() {
  const tiles = filteredTiles();
  state.filtered = tiles.flatMap(t => t.readings);
  if (!state.selectedId || !state.filtered.some(o=>o.id===state.selectedId))
    state.selectedId = primaryReading(tiles[0])?.id || null;
  syncGalleryToSelection();
  const o = selectedObs();
  if (o?.transition_id) state.selectedTransitionId = o.transition_id;
  renderHeader(); renderPager(); renderLifecycle(); renderGallery(); renderDetail();
  renderScenarios();
}

function syncGalleryToSelection() {
  const all = filteredTiles();
  if (!all.length) { state.galleryOffset = 0; return; }
  const maxOff = Math.max(0, all.length - GALLERY_PAGE);
  const idx = all.findIndex(t => t.readings.some(o => o.id === state.selectedId));
  if (idx < 0) {
    state.galleryOffset = Math.min(state.galleryOffset, maxOff);
    return;
  }
  if (idx < state.galleryOffset || idx >= state.galleryOffset + GALLERY_PAGE) {
    state.galleryOffset = Math.max(
      0,
      Math.min(idx - Math.floor(GALLERY_PAGE / 2), maxOff),
    );
  }
  state.galleryOffset = Math.max(0, Math.min(state.galleryOffset, maxOff));
}

/** Reveal a timeline/event target in the gallery even when filters would hide it. */
function ensureObservationVisible(oid) {
  const o = DATA.observations.find(x => x.id === oid);
  if (!o || !o.detector) return;
  let {t0, t1} = filterTimeRange();
  if (o.timestamp < t0) {
    $("filter-t0").value = Math.max(0, o.timestamp - 0.5).toFixed(1);
  }
  if (o.timestamp > t1) {
    $("filter-t1").value = (o.timestamp + 0.5).toFixed(1);
  }
  const conf=parseFloat($("filter-conf").value||"0");
  if (conf > 0 && (o.confidence==null || o.confidence < conf)) $("filter-conf").value = "0";
}

/** Select an observation from a timeline/chip click and page the gallery to it. */
function selectObservation(oid, opts = {}) {
  if (opts.transitionId) state.selectedTransitionId = opts.transitionId;
  let targetId = oid || null;
  if (!targetId && opts.timestamp != null && !Number.isNaN(opts.timestamp)) {
    const pool = DATA.observations.filter(o => o.detector);
    const prefer = opts.preferDetector
      ? pool.filter(o => o.detector === opts.preferDetector)
      : pool;
    const list = prefer.length ? prefer : pool;
    const near = list
      .slice()
      .sort((a, b) => Math.abs(a.timestamp - opts.timestamp) - Math.abs(b.timestamp - opts.timestamp))[0];
    if (near) targetId = near.id;
  }
  if (targetId) {
    state.selectedId = targetId;
    ensureObservationVisible(targetId);
    const o = DATA.observations.find(x => x.id === targetId);
    if (o?.transition_id && !opts.transitionId) state.selectedTransitionId = o.transition_id;
  }
  syncGalleryToSelection();
  renderPager();
  renderLifecycle();
  renderGallery();
  renderDetail();
  const evidence = document.getElementById("gallery");
  if (evidence) evidence.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function shiftGallery(delta) {
  const all = filteredTiles();
  if (!all.length) return;
  const maxOff = Math.max(0, all.length - GALLERY_PAGE);
  state.galleryOffset = Math.max(0, Math.min(maxOff, state.galleryOffset + delta));
  const tile = all[state.galleryOffset];
  const card = primaryReading(tile);
  if (card) {
    state.selectedId = card.id;
    if (card.transition_id) state.selectedTransitionId = card.transition_id;
  }
  renderGallery(); renderDetail(); renderPager();
}

function galleryWindowBounds() {
  const all = filteredTiles();
  if (!all.length) return null;
  const start = state.galleryOffset;
  const end = Math.min(all.length, start + GALLERY_PAGE);
  return { start, end, t0: all[start].timestamp, t1: all[end-1].timestamp, n: all.length };
}

function renderPager() {
  const win = galleryWindowBounds();
  const n = win ? win.n : 0;
  const pages = n ? Math.ceil(n / GALLERY_PAGE) : 0;
  const page = win ? Math.floor(win.start / GALLERY_PAGE) + 1 : 0;
  $("gallery-window").textContent = win
    ? `${page}/${pages} · ${win.start+1}–${win.end} / ${n} · ${fmtMmSs(win.t0,1)}–${fmtMmSs(win.t1,1)}`
    : "0/0";
  const maxOff = Math.max(0, n - GALLERY_PAGE);
  $("btn-gal-earlier").disabled = state.galleryOffset <= 0;
  $("btn-gal-later").disabled = !n || state.galleryOffset >= maxOff;
}

function chipLabel(o) {
  const name = (o.detector||"").toUpperCase();
  if (!o.positive) return `${name} −`;
  const score = o.confidence!=null ? o.confidence.toFixed(2) : "—";
  return `${name} ${score}+`;
}

function chipGtBadge(o) {
  const v = gtVerdict(o);
  if (!v || v.kind === "unmarked") return "";
  const letter = {hit:"HIT", miss:"MISS", fp:"FP", reject:"OK", mismatch:"≠"}[v.kind];
  if (!letter) return "";
  return `<span class="gtb ${esc(v.kind)}">${letter}</span>`;
}

function renderGallery() {
  const all = filteredTiles();
  const maxOff = Math.max(0, all.length - GALLERY_PAGE);
  state.galleryOffset = Math.max(0, Math.min(state.galleryOffset, maxOff));
  const start = state.galleryOffset;
  const cards = all.slice(start, start + GALLERY_PAGE);
  renderPager();
  $("gallery").innerHTML = cards.map(tile=>{
    const selected = isTileSelected(tile);
    const flags=new Set(tile.readings.flatMap(o => o.diagnostic_flags||[]));
    // GT disagreements are flagged inline so they stay findable while scrubbing.
    const flagCls = tile.readings.some(isFalsePositiveObs) ? "flag-fp"
      : tile.readings.some(isMissedObs) ? "flag-miss"
      : flags.has("detector_positive_event_negative") ? "flag-dpen" : "";
    const noThumb = !tile.image_relpath;
    const thumbCls = noThumb ? "no-thumb" : "";
    const img = tile.image_relpath
      ? `<img src="${esc(tile.image_relpath)}" loading="lazy"/>`
      : `<div class="ph">${tile.source==="cadence"?"cadence<br/>no JPEG":"no<br/>image"}</div>`;
    const chips = chipReadings(tile).map(o=>{
      const on = o.positive ? "on" : "";
      const picked = o.id===state.selectedId ? "picked" : "";
      const splat = o.detector==="splat" ? "splat-chip" : "";
      return `<button type="button" class="chip ${on} ${picked} ${splat}" data-oid="${esc(o.id)}" tabindex="-1">${esc(chipLabel(o))}${chipGtBadge(o)}</button>`;
    }).join("");
    const clickTarget = preferredReading(tile);
    return `<div class="card ${selected?"selected":""} ${flagCls} ${thumbCls}" data-oid="${esc(clickTarget?.id||"")}">${img}<span class="when">${fmtMmSs(tile.timestamp, 2)}</span><div class="chips">${chips}</div></div>`;
  }).join("") || "<div class='ph'>No cadence frames match filters</div>";
  $("gallery").querySelectorAll(".card").forEach(el=>el.onclick=()=>{
    selectObservation(el.getAttribute("data-oid"));
  });
  $("gallery").querySelectorAll(".chip").forEach(el=>el.onclick=(ev)=>{
    ev.stopPropagation();
    selectObservation(el.getAttribute("data-oid"));
  });
  $("gallery").querySelectorAll(".card.selected").forEach((el, i) => {
    if (i > 0) el.classList.remove("selected");
  });
  const selectedCard = $("gallery").querySelector(".card.selected");
  if (selectedCard) selectedCard.scrollIntoView({inline:"nearest", block:"nearest", behavior:"smooth"});
}

function renderScenarios() {
  const root = $("scenario-cards");
  if (!root) return;
  const cards = DATA.scenario_evidence || [];
  if (!cards.length) {
    root.innerHTML = `<div class="episode"><p class="howto">No scenario_contexts.json in this analysis folder.</p></div>`;
    renderReviewDetail();
    return;
  }
  root.innerHTML = cards.map(renderScenarioCard).join("");
  renderReviewDetail();
}

function renderScenarioCard(card, index) {
  const typ = scenarioTypeLabel(card);
  const range = `Video time: ${fmtMmSs(card.start_time, 1)} → ${fmtMmSs(card.end_time, 1)}`;
  const outcome = formatScenarioOutcome(card);
  const reviewing = index === state.reviewIndex ? "reviewing" : "";
  const reviewBtn = DATA.review_video_url
    ? `<div class="nav-row"><button type="button" data-review-index="${index}">Review</button></div>`
    : "";
  return `<article class="episode scenario-card ${reviewing}">
    <h3>${esc(typ)}</h3>
    <div class="meta"><span>${esc(range)}</span><span>${esc(outcome)}</span></div>
    ${reviewBtn}
    ${renderMembersBlock(card)}
    <div class="scenario-blocks">
      ${renderFactBlock("Timeline", scenarioTimelineRows(card.timeline))}
      ${renderFactBlock("Map", scenarioMapRows(card.map))}
      ${renderFactBlock("Splat observations", scenarioCombatRows(card.combat, Boolean(card.recovery)))}
      ${renderFactBlock("Death episode", scenarioRecoveryRows(card.recovery))}
      ${renderFactBlock("Relations", scenarioRelationRows(card.relations, card.following_death_id))}
      ${renderRosterBlock(card)}
    </div>
  </article>`;
}

function scenarioTypeLabel(card) {
  return String(card.scenario_type || card.scenario_id || "").replace(/-/g, "_").toUpperCase();
}

function formatScenarioOutcome(card) {
  if (!card.outcome) return "";
  const raw = String(card.outcome);
  if (!isEngagementCard(card)) return `Outcome: ${raw}`;
  if (raw === "died")
    return "Outcome: died (nearby death associated; DEATH not a member)";
  if (raw === "fragged")
    return "Outcome: fragged (no following death in window; not fight quality)";
  return `Outcome: ${raw}`;
}

function isEngagementCard(card) {
  const typ = String(card.scenario_type || "").toLowerCase();
  return typ === "engagement" || String(card.scenario_id || "").startsWith("engagement:");
}

function renderMembersBlock(card) {
  const footnote = isEngagementCard(card)
    ? `<p class="howto">SPLAT members only. A following DEATH is linked via relations / <code>following_death_id</code>; it belongs to DEATH_EPISODE.</p>`
    : "";
  return `<div class="scenario-members">
    <h2>Members</h2>
    <ul class="review-events">${renderReviewEvents(card.event_ids)}</ul>
    ${footnote}
  </div>`;
}

function bindReviewControls() {
  const section = $("scenario-review");
  const video = $("review-video");
  const note = $("review-video-note");
  if (!DATA.review_video_url) {
    if (section) section.hidden = true;
    return;
  }
  if (section) section.hidden = false;
  video.src = DATA.review_video_url;
  note.textContent = "";
  video.addEventListener("error", () => {
    note.textContent = "Could not load the review video. Check --video and that the local server is running.";
  });
  $("btn-review-prev").onclick = () => stepReview(-1);
  $("btn-review-next").onclick = () => stepReview(1);
  $("scenario-cards").addEventListener("click", event => {
    const btn = event.target.closest("[data-review-index]");
    if (!btn) return;
    reviewScenario(Number(btn.getAttribute("data-review-index")));
  });
}

function stepReview(delta) {
  const cards = DATA.scenario_evidence || [];
  if (!cards.length) return;
  const next = (state.reviewIndex + delta + cards.length) % cards.length;
  reviewScenario(next);
}

function reviewScenario(index) {
  const cards = DATA.scenario_evidence || [];
  if (!cards.length) return;
  state.reviewIndex = Math.max(0, Math.min(index, cards.length - 1));
  renderScenarios();
  $("scenario-review")?.scrollIntoView({behavior:"smooth", block:"start"});
  seekReviewVideo(cards[state.reviewIndex]);
}

function seekReviewVideo(card) {
  const video = $("review-video");
  if (!DATA.review_video_url || !card || !video) return;
  // Video currentTime maps directly to scenario.start_time (analysis seconds).
  const at = Math.max(0, Number(card.start_time || 0));
  const apply = () => {
    video.pause();
    video.currentTime = at;
  };
  if (video.readyState >= 1) apply();
  else video.addEventListener("loadedmetadata", apply, {once:true});
}

function renderReviewDetail() {
  const root = $("review-detail");
  if (!root) return;
  if (!DATA.review_video_url) {
    root.innerHTML = "";
    return;
  }
  const cards = DATA.scenario_evidence || [];
  const card = cards[state.reviewIndex];
  if (!card) {
    root.innerHTML = `<div class="episode"><p class="howto">No scenarios to review.</p></div>`;
    return;
  }
  const typ = scenarioTypeLabel(card);
  const range = `Video time: ${fmtMmSs(card.start_time, 1)} → ${fmtMmSs(card.end_time, 1)}`;
  const outcome = formatScenarioOutcome(card);
  root.innerHTML = `<article class="episode">
    <h3>${esc(typ)}</h3>
    <div class="meta"><span>${esc(range)}</span><span>${esc(outcome)}</span></div>
    ${renderMembersBlock(card)}
    <div class="scenario-blocks">
      ${renderFactBlock("Timeline", scenarioTimelineRows(card.timeline))}
      ${renderFactBlock("Map", scenarioMapRows(card.map))}
      ${renderFactBlock("Splat observations", scenarioCombatRows(card.combat, Boolean(card.recovery)))}
      ${renderFactBlock("Death episode", scenarioRecoveryRows(card.recovery))}
      ${renderFactBlock("Relations", scenarioRelationRows(card.relations, card.following_death_id))}
      ${renderRosterBlock(card)}
    </div>
  </article>`;
}

function renderReviewEvents(eventIds) {
  const rows = (eventIds || []).map(parseScenarioEventId).filter(item => item);
  if (!rows.length) return `<li><span class="t">—</span><span>No event_ids on this scenario</span></li>`;
  return rows.map(item => `<li><span class="t">${esc(fmtMmSs(item.time, 1))}</span><span>${esc(item.label)}</span></li>`).join("");
}

function parseScenarioEventId(eventId) {
  const parts = String(eventId || "").split(":");
  if (parts.length < 2) return null;
  const time = Number(parts[1]);
  if (Number.isNaN(time)) return null;
  return { time, label: String(parts[0] || "").replace(/-/g, "_").toUpperCase() };
}

function renderFactBlock(title, rows) {
  if (!rows.length) return "";
  const body = rows.map(([k, v]) => `<span>${esc(k)}</span><span>${esc(v)}</span>`).join("");
  return `<div class="layer"><h3>${esc(title)}</h3><div class="kv">${body}</div></div>`;
}

const ROSTER_WINDOW_OFFSETS = [-5, -2, 0, 3, 6];
const ROSTER_MAX_GAP = 1.0;

function formatRosterAvB(ally, opponent) {
  if (ally == null || opponent == null) return "—";
  const diff = Number(ally) - Number(opponent);
  const label = `${ally}v${opponent}`;
  if (diff === 0) return label;
  const sign = diff > 0 ? "+" : "";
  return `${label} (${sign}${diff})`;
}

function nearestRosterSample(videoTime, maxGap=ROSTER_MAX_GAP) {
  const series = DATA.roster_timeline || [];
  let best = null;
  let bestGap = Infinity;
  for (const sample of series) {
    const gap = Math.abs(Number(sample.video_time) - Number(videoTime));
    if (gap > maxGap) continue;
    if (gap < bestGap) {
      best = sample;
      bestGap = gap;
      if (gap === 0) break;
    }
  }
  return best;
}

function scenarioRosterAnchor(card) {
  const typ = String(card.scenario_type || "").toLowerCase();
  if (typ === "death_episode") {
    const death = card.recovery?.death_time;
    if (death != null) return Number(death);
    return card.start_time == null ? null : Number(card.start_time);
  }
  if (typ === "engagement") {
    const first = card.combat?.first_splat_time;
    if (first != null) return Number(first);
    return card.start_time == null ? null : Number(card.start_time);
  }
  return null;
}

function compressRosterTrajectory(anchor) {
  const series = DATA.roster_timeline || [];
  const lo = Number(anchor) + Math.min(...ROSTER_WINDOW_OFFSETS);
  const hi = Number(anchor) + Math.max(...ROSTER_WINDOW_OFFSETS);
  const inRange = series.filter(s => {
    const t = Number(s.video_time);
    return t >= lo - 1e-9 && t <= hi + 1e-9;
  });
  const rows = [];
  let prevKey = null;
  for (const sample of inRange) {
    const key = `${sample.ally_alive_count}v${sample.opponent_alive_count}`;
    const isAnchor = Math.abs(Number(sample.video_time) - Number(anchor)) < 1e-9;
    if (prevKey === key && !isAnchor) continue;
    rows.push({
      time: Number(sample.video_time),
      label: formatRosterAvB(sample.ally_alive_count, sample.opponent_alive_count),
      isAnchor,
    });
    prevKey = key;
  }
  if (!rows.some(r => r.isAnchor)) {
    const at = nearestRosterSample(anchor);
    if (at) {
      rows.push({
        time: Number(anchor),
        label: formatRosterAvB(at.ally_alive_count, at.opponent_alive_count),
        isAnchor: true,
      });
      rows.sort((a, b) => a.time - b.time);
    }
  }
  return rows;
}

function renderRosterBlock(card) {
  const anchor = scenarioRosterAnchor(card);
  if (anchor == null) return "";
  const windowRows = ROSTER_WINDOW_OFFSETS.map(offset => {
    const t = Number(anchor) + Number(offset);
    const sample = nearestRosterSample(t);
    const mark = offset === 0 ? " ← anchor" : "";
    const avb = sample
      ? formatRosterAvB(sample.ally_alive_count, sample.opponent_alive_count)
      : "—";
    return [`${offset >= 0 ? "+" : ""}${offset.toFixed(1)}s (${fmtMmSs(t, 1)})`, `${avb}${mark}`];
  });
  const traj = compressRosterTrajectory(anchor);
  const trajRows = traj.map(row => [
    fmtMmSs(row.time, 1),
    `${row.label}${row.isAnchor ? " ← anchor" : ""}`,
  ]);
  return (
    renderFactBlock("Player-count window", windowRows) +
    renderFactBlock("Player-count transitions", trajRows)
  );
}

function scenarioTimelineRows(nest) {
  if (!nest) return [];
  return [
    ["Previous death", fmtSeconds(nest.time_since_previous_death)],
    ["Next death", fmtSeconds(nest.time_to_next_death)],
  ];
}

function scenarioMapRows(nest) {
  if (!nest) return [];
  const rows = [["Checks during scenario", fmtCount(nest.map_check_count ?? nest.map_checks_during_scenario)]];
  pushIfPresent(rows, "Before start", nest.map_checks_before_start, fmtCount);
  pushIfPresent(rows, "Any map overlay before death", nest.map_check_before_death, fmtYesNo);
  pushIfPresent(rows, "During death episode", nest.map_checks_during_death_episode, fmtCount);
  pushIfPresent(rows, "Checked while dead", nest.map_checked_while_dead, fmtYesNo);
  pushIfPresent(rows, "After active again", nest.map_checks_after_active_again, fmtCount);
  pushIfPresent(rows, "In death episode", nest.in_death_episode, fmtYesNo);
  if (nest.seconds_since_map_check_before_death != null)
    rows.push(["Gap since last map before death (unbounded)", fmtSeconds(nest.seconds_since_map_check_before_death)]);
  if (nest.last_map_before_death_event_id)
    rows.push(["Last map before death", String(nest.last_map_before_death_event_id)]);
  if (Array.isArray(nest.map_event_ids_during_episode) && nest.map_event_ids_during_episode.length)
    rows.push(["Maps during episode", String(nest.map_event_ids_during_episode.length)]);
  return rows;
}

function scenarioCombatRows(nest, showFirstSplatDash) {
  if (!nest) return [];
  const rows = [["Splats", fmtCount(nest.splat_count)]];
  pushIfPresent(rows, "First splat", nest.first_splat_time, fmtMmSsOrDash);
  pushIfPresent(rows, "Last splat", nest.last_splat_time, fmtMmSsOrDash);
  pushIfPresent(rows, "Duration", nest.duration, fmtSeconds);
  if (showFirstSplatDash || nest.time_to_first_splat != null)
    rows.push(["Time to first splat", fmtSeconds(nest.time_to_first_splat)]);
  pushIfPresent(rows, "Time between first/last splat", nest.time_to_last_splat, fmtSeconds);
  pushIfPresent(rows, "Splat–death gap", nest.splat_death_gap, fmtSeconds);
  if (nest.trade_candidate != null)
    rows.push(["Trade-window flag", nest.trade_candidate ? "true" : "false"]);
  return rows;
}

function scenarioRecoveryRows(nest) {
  if (!nest) return [];
  const toRespawn = nest.death_to_respawn ?? nest.time_to_respawn;
  const toActive = nest.death_to_active_again ?? nest.time_to_active_again;
  const respawnToActive = nest.respawn_to_active_again;
  return [
    ["Death → respawn", fmtSeconds(toRespawn)],
    ["Death → active again", fmtSeconds(toActive)],
    ["Respawn → active again", fmtSeconds(respawnToActive)],
    ["Awaiting duration", fmtSeconds(nest.awaiting_duration)],
    ["Complete", nest.complete == null ? "—" : (nest.complete ? "Yes" : "No")],
    ["Respawn reason", nest.respawn_reason == null ? "—" : String(nest.respawn_reason)],
  ];
}

function scenarioRelationRows(nest, followingDeathId) {
  if (!nest && !followingDeathId) return [];
  const rows = [];
  if (followingDeathId)
    rows.push(["Following death (compat id)", String(followingDeathId)]);
  if (!nest) return rows;
  if (nest.leads_to_death_episode_id)
    rows.push(["Associated death episode (temporal rule)", String(nest.leads_to_death_episode_id)]);
  if (nest.follows_death_episode_id)
    rows.push(["Follows death episode (temporal)", String(nest.follows_death_episode_id)]);
  if (nest.preceded_by_engagement_id)
    rows.push(["Preceded by engagement", String(nest.preceded_by_engagement_id)]);
  if (nest.next_engagement_id)
    rows.push(["Next engagement", String(nest.next_engagement_id)]);
  return rows;
}

function fmtMmSsOrDash(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return fmtMmSs(Number(value), 1);
}

function pushIfPresent(rows, label, value, format) {
  if (value == null) return;
  rows.push([label, format(value)]);
}

function fmtSeconds(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return `${Number(value).toFixed(1)}s`;
}

function fmtCount(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return String(Number(value));
}

function fmtYesNo(value) {
  if (value == null) return "—";
  return value ? "Yes" : "No";
}

function humanizeId(value) {
  if (value == null || value === "") return "—";
  return String(value).replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

function renderHeader() {
  const s=DATA.summary;
  const wanted = ["death","respawn","active_again","splat"];
  const events = wanted.map(k => `${k} ${(s.event_counts||{})[k]||0}`).join(" · ");
  const id = DATA.match_identity;
  const mode = humanizeId(id?.battle_mode_id);
  const stage = humanizeId(id?.stage_id);
  let status = `<span class="status warn">identity missing</span>`;
  if (id) {
    status = id.resolved
      ? `<span class="status ok">resolved${id.resolved_at!=null?` @ ${fmtMmSs(id.resolved_at,1)}`:""}</span>`
      : `<span class="status warn">unresolved</span>`;
  }
  $("match-info").innerHTML =
    `<div><span class="label">Mode</span><span class="value">${esc(mode)}</span></div>` +
    `<div><span class="label">Stage</span><span class="value">${esc(stage)}</span></div>` +
    status;
  $("header-meta").innerHTML =
    `<span>${esc(s.video_label)}</span>` +
    `<span>${fmtMmSs(s.duration_seconds, 1)}</span>` +
    `<span>${s.frame_count} cadence frames</span>` +
    `<span>${esc(events)}</span>` +
    `<span>death confirmed ${s.death_confirmed||0} · suppressed ${s.death_suppressed||0}</span>`;
  $("warnings").innerHTML = (s.warnings||[]).map(w=>`<div class="${w.level}">${w.level==="ok"?"✓":"⚠"} ${esc(w.message)}</div>`).join("");
}

function bindTimelineJump(root) {
  root.querySelectorAll("[data-ts]").forEach(el=>{
    el.onclick=()=>{
      const tid=el.getAttribute("data-tid");
      const ts=parseFloat(el.getAttribute("data-ts"));
      const prefer=el.getAttribute("data-prefer") || "";
      const tr=(DATA.transitions||[]).find(t=>t.id===tid);
      selectObservation(tr?.observation_id || null, {
        transitionId: tid || null,
        timestamp: Number.isNaN(ts) ? null : ts,
        preferDetector: prefer || undefined,
      });
    };
  });
}

function renderSplatLane(duration) {
  const events = (DATA.lifecycle_marks||[]).filter(m =>
    String(m.event_label||"").toUpperCase()==="SPLAT"
  );
  const eventTs = new Set(events.map(m => Number(m.timestamp).toFixed(3)));
  const positives = (DATA.observations||[]).filter(o =>
    o.detector==="splat" && o.positive && !eventTs.has(Number(o.timestamp).toFixed(3))
  );
  const badges = events.map(m=>{
    const left=(m.timestamp/duration)*100;
    const sel=m.transition_id && m.transition_id===state.selectedTransitionId ? "selected":"";
    return `<button type="button" class="splat-badge ${sel}" style="left:${left}%" title="${esc(fmtTime(m.timestamp, 2))} · SPLAT event" data-tid="${esc(m.transition_id||"")}" data-ts="${m.timestamp}" data-prefer="splat">SPLAT</button>`;
  }).join("");
  const ticks = positives.map(o=>{
    const left=(o.timestamp/duration)*100;
    const picked = o.id===state.selectedId ? "selected":"";
    return `<button type="button" class="splat-tick ${picked}" style="left:${left}%" title="${esc(fmtTime(o.timestamp, 2))} · splat detector +" data-oid="${esc(o.id)}" data-ts="${o.timestamp}" data-prefer="splat"></button>`;
  }).join("");
  return `<div class="splat-row"><span class="lane-name">Splat</span>${badges}${ticks}</div>`;
}

function fmtFracPct(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return `${Math.round(Number(value) * 100)}%`;
}

function renderMapInkLane(duration) {
  const samples = DATA.map_ink_timeline || [];
  if (!samples.length) {
    return `<div class="metric-row map-ink"><span class="lane-name">Map ink</span><span style="position:absolute;left:72px;top:10px;font-size:10px;color:var(--muted)">no map_observations.json</span></div>`;
  }
  const ticks = samples.map(s=>{
    const left=(s.video_time/duration)*100;
    const tip = `${fmtTime(s.video_time, 2)} · A=${fmtFracPct(s.ally_classified_fraction)} O=${fmtFracPct(s.opponent_classified_fraction)} C=${fmtFracPct(s.classified_fraction)}`;
    const nearSel = state.selectedId && Math.abs((selectedObs()?.timestamp??-1e9) - s.video_time) < 0.6;
    return `<button type="button" class="map-ink-tick ${nearSel?"selected":""}" style="left:${left}%" title="${esc(tip)}" data-ts="${s.video_time}" data-prefer="map_overlay"></button>`;
  }).join("");
  return `<div class="metric-row map-ink"><span class="lane-name">Map ink</span>${ticks}</div>`;
}

function renderSpecialLane(duration) {
  const samples = (DATA.observations||[]).filter(o =>
    o.detector==="special_gauge" && o.reading && o.reading.visible
  );
  if (!samples.length) {
    return `<div class="metric-row special"><span class="lane-name">Special</span><span style="position:absolute;left:72px;top:10px;font-size:10px;color:var(--muted)">no visible gauge samples</span></div>`;
  }
  const ticks = samples.map(o=>{
    const left=(o.timestamp/duration)*100;
    const ready = !!o.reading.ready;
    const label = ready ? "READY" : fmtFracPct(o.reading.fill_fraction);
    const picked = o.id===state.selectedId ? "selected":"";
    const tip = ready
      ? `${fmtTime(o.timestamp, 2)} · READY`
      : `${fmtTime(o.timestamp, 2)} · fill≈${fmtFracPct(o.reading.fill_fraction)} (approx)`;
    return `<button type="button" class="special-tick ${ready?"ready":""} ${picked}" style="left:${left}%" title="${esc(tip)}" data-oid="${esc(o.id)}" data-ts="${o.timestamp}" data-prefer="special_gauge"><span class="lbl">${esc(label)}</span></button>`;
  }).join("");
  return `<div class="metric-row special"><span class="lane-name">Special</span>${ticks}</div>`;
}

function nearestMapInkSample(timestamp, maxGapSeconds=2.0) {
  const series = DATA.map_ink_timeline || [];
  if (!series.length || timestamp == null) return null;
  let best = null;
  let bestGap = Infinity;
  for (const s of series) {
    const gap = Math.abs(Number(s.video_time) - Number(timestamp));
    if (gap < bestGap) { bestGap = gap; best = s; }
  }
  if (!best || bestGap > maxGapSeconds) return null;
  return best;
}

function renderMapInkPanel(timestamp) {
  const s = nearestMapInkSample(timestamp);
  if (!s) {
    return `<div class="panel"><h3>Map ink</h3><div class="kv"><span>sample</span><strong>none near this time</strong></div></div>`;
  }
  return `<div class="panel"><h3>Map ink</h3><div class="kv">
    <span>video_time</span><strong>${fmtTime(s.video_time, 3)}</strong>
    <span>stage_id</span><strong>${esc(s.stage_id||"—")}</strong>
    <span>battle_mode_id</span><strong>${esc(s.battle_mode_id||"—")}</strong>
    <span>ally_classified_fraction</span><strong>${num(s.ally_classified_fraction)}</strong>
    <span>opponent_classified_fraction</span><strong>${num(s.opponent_classified_fraction)}</strong>
    <span>classified_fraction</span><strong>${num(s.classified_fraction)}</strong>
    <span>confidence</span><strong>${num(s.confidence)}</strong>
    <span>ally_ink_pixels</span><strong>${s.ally_ink_pixels??"—"}</strong>
    <span>opponent_ink_pixels</span><strong>${s.opponent_ink_pixels??"—"}</strong>
    <span>classified_pixels</span><strong>${s.classified_pixels??"—"}</strong>
    <span>total_sample_pixels</span><strong>${s.total_sample_pixels??"—"}</strong>
    <span>unclassified_pixels</span><strong>${s.unclassified_pixels??"—"}</strong>
  </div></div>`;
}

function renderSpecialGaugePanel(o) {
  if (!o || o.detector !== "special_gauge") {
    const near = (DATA.observations||[]).filter(x =>
      x.detector==="special_gauge" && Math.abs(x.timestamp - (o?.timestamp??-1e9)) < 0.6
    ).sort((a,b)=>Math.abs(a.timestamp-(o?.timestamp??0))-Math.abs(b.timestamp-(o?.timestamp??0)))[0];
    if (!near) {
      return `<div class="panel"><h3>Special gauge</h3><div class="kv"><span>sample</span><strong>none near this time</strong></div></div>`;
    }
    return renderSpecialGaugePanel(near);
  }
  const r = o.reading || {};
  return `<div class="panel"><h3>Special gauge</h3><div class="kv">
    <span>visible</span><strong>${r.visible?"✓ true":"✗ false"}</strong>
    <span>fill_fraction</span><strong>${r.fill_fraction==null?"—":num(r.fill_fraction)}</strong>
    <span>ready</span><strong>${r.ready?"✓ true":"✗ false"}</strong>
    <span>confidence</span><strong>${num(o.confidence)}</strong>
    <span>dial_score</span><strong>${num(r.dial_score)}</strong>
    <span>lit_sector_fraction</span><strong>${num(r.lit_sector_fraction)}</strong>
    <span>ready_prompt_score</span><strong>${num(r.ready_prompt_score)}</strong>
    <span>timestamp</span><strong>${r.timestamp==null?"—":fmtTime(r.timestamp, 3)}</strong>
  </div></div>`;
}

function renderLifecycle() {
  const duration=Math.max(DATA.summary.duration_seconds,1);
  const width=Math.max(720, Math.ceil(duration*8));
  const ticks=[];
  const step=duration>180?30:duration>60?20:10;
  for (let t=0;t<=duration+0.01;t+=step) ticks.push(`<div class="tick" style="left:${(t/duration)*100}%">${fmtMmSs(t)}</div>`);
  const segs=(DATA.lifecycle_segments||[]).map(seg=>{
    const left=(seg.start/duration)*100, w=Math.max(((seg.end-seg.start)/duration)*100,0.15);
    return `<div class="life-seg ${esc(seg.phase)}" style="left:${left}%;width:${w}%" title="${esc(seg.phase)}"></div>`;
  }).join("");

  // Lifecycle labels only. SPLAT is orthogonal and has its own lane below.
  const minGapPx = 96;
  const laneTops = [];
  const placed = (DATA.lifecycle_marks||[])
    .filter(m => String(m.event_label||"").toUpperCase()!=="SPLAT")
    .slice()
    .sort((a,b)=>a.timestamp-b.timestamp)
    .map(m=>{
    const leftPct = (m.timestamp/duration)*100;
    const x = (leftPct/100)*width;
    let lane = 0;
    while (lane < laneTops.length && (x - laneTops[lane]) < minGapPx) lane += 1;
    if (lane === laneTops.length) laneTops.push(-1e9);
    laneTops[lane] = x;
    return { m, leftPct, lane };
  });
  const maxLane = placed.length ? Math.max(...placed.map(p=>p.lane)) : 0;
  const rowH = 36 + (maxLane + 1) * 44;

  const marks = placed.map(({m, leftPct, lane})=>{
    const sel=m.transition_id && m.transition_id===state.selectedTransitionId ? "selected":"";
    const compact = maxLane >= 2 ? "compact" : "";
    const tip = `${fmtTime(m.timestamp, 2)} · ${m.event_label} → ${(m.phase_label||"").toUpperCase()}`;
    const phase = String(m.phase_label||"").toLowerCase().replace(/_/g, "-");
    const top = 26 + lane * 44;
    return `<div class="life-mark ${esc(phase)} ${sel} ${compact}" style="left:${leftPct}%;top:${top}px" title="${esc(tip)}" data-tid="${esc(m.transition_id||"")}" data-ts="${m.timestamp}">
      <div class="dot"></div><div class="stem"></div>
      <div class="lbl"><div class="t">${fmtMmSs(m.timestamp, 1)}</div><div class="e">${esc(m.event_label)}</div></div>
    </div>`;
  }).join("");

  $("lifecycle-strip").innerHTML =
    `<div style="min-width:${width}px"><div class="axis">${ticks.join("")}</div>` +
    `<div class="life-row" style="height:${rowH}px">${segs}${marks}</div>` +
    `${renderSplatLane(duration)}${renderMapInkLane(duration)}${renderSpecialLane(duration)}</div>`;
  bindTimelineJump($("lifecycle-strip"));
  $("lifecycle-strip").querySelectorAll(".splat-tick[data-oid], .special-tick[data-oid]").forEach(el=>{
    el.onclick=(ev)=>{
      ev.stopPropagation();
      selectObservation(el.getAttribute("data-oid"), {
        timestamp: parseFloat(el.getAttribute("data-ts")),
        preferDetector: el.getAttribute("data-prefer") || undefined,
      });
    };
  });
  $("lifecycle-strip").querySelectorAll(".map-ink-tick[data-ts]").forEach(el=>{
    el.onclick=(ev)=>{
      ev.stopPropagation();
      selectObservation(null, {
        timestamp: parseFloat(el.getAttribute("data-ts")),
        preferDetector: "map_overlay",
      });
    };
  });
}

function selectedObs(){ return DATA.observations.find(o=>o.id===state.selectedId)||null; }
function selectedTransition(){
  if (state.selectedTransitionId) {
    const t=(DATA.transitions||[]).find(x=>x.id===state.selectedTransitionId);
    if (t) return t;
  }
  const o=selectedObs();
  if (o?.transition_id) return (DATA.transitions||[]).find(x=>x.id===o.transition_id)||null;
  if (!o) return null;
  const list=DATA.transitions||[];
  if (!list.length) return null;
  return list.slice().sort((a,b)=>Math.abs(a.timestamp-o.timestamp)-Math.abs(b.timestamp-o.timestamp))[0];
}

function step(dir){
  const list=filteredTiles(); if(!list.length) return;
  let idx=list.findIndex(t=>t.readings.some(o=>o.id===state.selectedId)); if(idx<0) idx=0;
  const next=list[(idx+dir+list.length)%list.length];
  const reading=primaryReading(next);
  if (!reading) return;
  selectObservation(reading.id, { transitionId: reading.transition_id || null });
}

function renderDetail(){
  const o=selectedObs(); const tr=selectedTransition(); const diag=diagFor(o);
  const img=$("frame-img"), ph=$("frame-ph"), overlay=$("roi-overlay");
  if (!o){ ph.hidden=false; img.hidden=true; overlay.hidden=true; $("obs-meta").innerHTML=""; $("reading").innerHTML=""; $("causal").hidden=true; $("diag-panel").hidden=true; renderGtContext(null); return; }
  if (o.image_relpath){ ph.hidden=true; img.hidden=false; img.onload=()=>updateRoi(o); img.src=o.image_relpath; }
  else {
    ph.hidden=false; img.hidden=true; overlay.hidden=true;
    if (o.source === "cadence") {
      ph.innerHTML = `Cadence frame — no JPEG saved<br/><span style="opacity:.8">Detectors ran in-memory. Re-analyze with --debug-persist-cadence-frames to keep thumbnails.</span>`;
    } else if (o.frame_path) {
      ph.innerHTML = `Image file missing on disk<br/><code style="font-size:10px">${esc(o.frame_path)}</code>`;
    } else {
      ph.innerHTML = `No saved image for this observation<br/><span style="opacity:.8">source=${esc(o.source||"unknown")}</span>`;
    }
  }
  $("obs-meta").innerHTML = `
    <span>Timestamp</span><strong>${fmtTime(o.timestamp, 3)}</strong>
    <span>Confidence</span><strong>${num(o.confidence)}</strong>`;
  $("layers").innerHTML = `
    <div class="layer"><h3>1. Raw observation</h3><div class="kv">
      <span>detector</span><strong>${esc(o.detector||"—")}</strong>
      <span>positive</span><strong>${o.positive}</strong>
      <span>score</span><strong>${num(o.reading.presence_score??o.reading.score??o.confidence)}</strong>
    </div></div>
    <div class="layer"><h3>2. Fused state</h3><div class="kv">
      <span>match_phase</span><strong>${esc(o.match_phase||"—")}</strong>
      <span>player_lifecycle</span><strong>${esc(o.lifecycle||"—")}</strong>
      <span>active_gameplay</span><strong>${o.active_gameplay??"—"}</strong>
      <span>player_alive</span><strong>${o.player_alive??"—"}</strong>
      <span>roster</span><strong>${esc(formatRosterAvB(o.ally_alive_count, o.opponent_alive_count))}</strong>
    </div></div>
    <div class="layer"><h3>3. Events</h3><div class="kv">
      <span>nearby</span><strong>${esc(tr?.title||"—")}</strong>
      <span>transition</span><strong>${esc((tr?.from_phase||"?")+" → "+(tr?.to_phase||"?"))}</strong>
    </div></div>`;
  renderCausal(diag);
  renderDeathDiag(diag, o);
  $("reading").innerHTML =
    `<div class="side-metrics">${renderMapInkPanel(o.timestamp)}${renderSpecialGaugePanel(o)}</div>` +
    renderReading(o);
  const raw=$("raw-json"); raw.textContent=JSON.stringify({observation:o, transition:tr, diagnostic:diag, map_ink:nearestMapInkSample(o.timestamp)}, null, 2);
  raw.classList.toggle("open", state.showRaw);
  $("btn-raw").classList.toggle("active", state.showRaw);
  $("btn-roi").classList.toggle("active", state.showRoi);
  $("roi-panel").classList.toggle("open", state.showRoi && !!o.roi);
  if (state.showRoi && o.roi) {
    $("roi-kv").innerHTML=`<span>x1</span><strong>${o.roi.x1.toFixed(3)}</strong><span>y1</span><strong>${o.roi.y1.toFixed(3)}</strong><span>x2</span><strong>${o.roi.x2.toFixed(3)}</strong><span>y2</span><strong>${o.roi.y2.toFixed(3)}</strong>`;
    drawRoi(o);
  }
  updateRoi(o);
  renderGtContext(o);
}

function renderCausal(diag){
  const box=$("causal");
  if (!diag){ box.hidden=true; return; }
  box.hidden=false;
  box.className = "causal " + (diag.decision==="confirmed"?"ok":(diag.decision==="suppressed"?"warn":"bad"));
  const steps=(diag.causal||[]).map((s,i)=>{
    const arrow = i < (diag.causal.length-1) ? `<div class="arrow">↓</div>` : "";
    return `<div class="chain-step ${s.ok?"ok":"bad"}">
      <strong>${esc(s.title)}</strong>
      <div>${s.ok?"✓":"✗"} ${esc(s.detail)}</div>
      ${s.reason?`<div style="color:var(--muted);margin-top:4px">reason: ${esc(s.reason)}</div>`:""}
    </div>${arrow}`;
  }).join("");
  box.innerHTML = `<h3>Detector → Lifecycle → Event</h3>${steps}`;
}

function renderDeathDiag(diag, o){
  const box=$("diag-panel");
  if (!diag || o.detector!=="death"){ box.hidden=true; return; }
  box.hidden=false;
  const cues=(diag.cues||[]).map(c=>{
    const v = c.value==null ? 0 : Number(c.value);
    const pct = Math.max(0, Math.min(100, v*100));
    const passCls = c.passed===true?"pass":(c.passed===false?"fail":"");
    const mark = c.passed===true?"✓":(c.passed===false?"✗":"·");
    return `<div class="cue-row"><span>${esc(c.label)}</span><div class="bar"><i class="${passCls}" style="width:${pct}%"></i></div><span>${c.value==null?"—":v.toFixed(2)}</span><span class="${c.passed===true?"check ok":(c.passed===false?"check bad":"")}">${mark}</span></div>`;
  }).join("");
  const rules=(diag.rule_lines||[]).map(r=>`<div class="check ${r.ok?"ok":"bad"}">${r.ok?"PASS":"FAIL"}  ${esc(r.label)}</div>`).join("");
  const dec = (diag.decision||"rejected").toUpperCase();
  box.innerHTML = `<h3>Death diagnostic</h3>
    ${cues}
    <div style="margin-top:10px;color:var(--muted)">Why wasn't / was this a death?</div>
    ${rules}
    <div class="decision ${esc(diag.decision)}">${diag.decision==="confirmed"?"✓":"✗"} ${esc(dec)}
      <span class="sub">${esc(diag.decision_reason||"")}</span>
    </div>`;
}

function renderReading(o){
  const r=o.reading||{};
  if (!Object.keys(r).length) {
    return `<h2>${esc(DETECTOR_TITLE[o.detector]||o.detector||"Reading")}</h2><div class="kv"><span>reading</span><strong>absent on this frame</strong></div>`;
  }
  if (o.detector==="respawn") return `<h2>Respawn reading</h2><div class="kv">
    <span>detected</span><strong>${r.detected?"✓ true":"✗ false"}</strong>
    <span>template_score</span><strong>${num(r.template_score)}</strong>
    <span>countdown_value</span><strong>${r.countdown_value??"—"}</strong>
    <span>evidence_type</span><strong>${esc(r.evidence_type||"—")}</strong>
    <span>ocr_text</span><strong>${esc(r.ocr_text||"—")}</strong>
    <span>presence_score</span><strong>${num(r.presence_score)}</strong>
    <span>dark_frac</span><strong>${num(r.dark_frac)}</strong>
    <span>bright_frac</span><strong>${num(r.bright_frac)}</strong>
    <span>p95</span><strong>${num(r.p95)}</strong></div>`;
  if (o.detector==="death") {
    const thr=(DATA.death_thresholds||{}).banner_dark_threshold??0.72;
    const whiteThr=(DATA.death_thresholds||{}).ouch_white_ratio??0.12;
    const ouchThr=(DATA.death_thresholds||{}).ouch_match_threshold??0.70;
    return `<h2>Death reading</h2><div class="kv">
      <span>detected</span><strong>${r.detected}</strong>
      <span>ouch_detected (glyph)</span><strong>${r.ouch_detected}</strong>
      <span>ouch_template_score</span><strong>${num(r.ouch_template_score)} <span style="color:var(--muted)">(≥${Number(ouchThr).toFixed(2)})</span></strong>
      <span>ouch_white_score</span><strong>${num(r.ouch_white_score)} <span style="color:var(--muted)">(≥${Number(whiteThr).toFixed(2)}, not sufficient alone)</span></strong>
      <span>ouch_heuristic</span><strong>${r.ouch_heuristic??"—"}</strong>
      <span>banner_detected</span><strong>${r.banner_detected}</strong>
      <span>banner_template_score</span><strong>${num(r.banner_template_score)}</strong>
      <span>banner_dark_score</span><strong>${num(r.banner_dark_score)} <span style="color:var(--muted)">(≥${Number(thr).toFixed(2)}, supporting)</span></strong>
    </div>`;
  }
  if (o.detector==="active_gameplay") return `<h2>ActiveGameplay reading (HUD evidence)</h2><div class="kv"><span>detected</span><strong>${r.detected}</strong><span>score</span><strong>${num(r.score)}</strong><span>weapon edge</span><strong>${num(r.weapon_edge_frac)}</strong><span>hud edge</span><strong>${num(r.hud_edge_frac)}</strong><span>return_control</span><strong>${r.return_control}</strong></div>`;
  if (o.detector==="splat") return `<h2>Splat reading</h2><div class="kv"><span>detected</span><strong>${r.detected}</strong><span>skull</span><strong>${num(r.skull_score)}</strong><span>text</span><strong>${num(r.text_score)}</strong><span>color</span><strong>${num(r.adjacent_color_score)}</strong></div>`;
  if (o.detector==="map_overlay") return `<h2>Map overlay reading</h2><div class="kv"><span>present</span><strong>${r.present}</strong><span>template_score</span><strong>${num(r.template_score)}</strong><span>map edge</span><strong>${num(r.map_edge_frac)}</strong></div>`;
  if (o.detector==="special_gauge") return `<h2>Special gauge reading</h2><div class="kv">
    <span>visible</span><strong>${r.visible?"✓ true":"✗ false"}</strong>
    <span>fill_fraction</span><strong>${r.fill_fraction==null?"—":num(r.fill_fraction)}</strong>
    <span>ready</span><strong>${r.ready?"✓ true":"✗ false"}</strong>
    <span>confidence</span><strong>${num(o.confidence)}</strong>
    <span>dial_score</span><strong>${num(r.dial_score)}</strong>
    <span>lit_sector_fraction</span><strong>${num(r.lit_sector_fraction)}</strong>
    <span>ready_prompt_score</span><strong>${num(r.ready_prompt_score)}</strong>
    <span>timestamp</span><strong>${r.timestamp==null?"—":fmtTime(r.timestamp, 3)}</strong>
  </div>`;
  return `<pre class="raw open">${esc(JSON.stringify(r,null,2))}</pre>`;
}

function updateRoi(o){
  const img=$("frame-img"), overlay=$("roi-overlay");
  if (!state.showRoi || !o.roi || img.hidden || !img.naturalWidth){ overlay.hidden=true; return; }
  const stage=$("frame-stage");
  const r=img.getBoundingClientRect(), s=stage.getBoundingClientRect();
  overlay.hidden=false;
  overlay.style.left=((r.left-s.left)+o.roi.x1*r.width)+"px";
  overlay.style.top=((r.top-s.top)+o.roi.y1*r.height)+"px";
  overlay.style.width=Math.max((o.roi.x2-o.roi.x1)*r.width,2)+"px";
  overlay.style.height=Math.max((o.roi.y2-o.roi.y1)*r.height,2)+"px";
}

function drawRoi(o){
  if (!o.image_relpath || !o.roi) return;
  const source=new Image();
  source.onload=()=>{
    const x1=Math.floor(o.roi.x1*source.naturalWidth), y1=Math.floor(o.roi.y1*source.naturalHeight);
    const x2=Math.ceil(o.roi.x2*source.naturalWidth), y2=Math.ceil(o.roi.y2*source.naturalHeight);
    const w=Math.max(1,x2-x1), h=Math.max(1,y2-y1);
    const canvas=$("roi-canvas"); canvas.width=480; canvas.height=Math.max(80,Math.round(480*h/w));
    const ctx=canvas.getContext("2d"); ctx.fillStyle="#0b0d10"; ctx.fillRect(0,0,canvas.width,canvas.height);
    ctx.drawImage(source,x1,y1,w,h,0,0,canvas.width,canvas.height);
  };
  source.src=o.image_relpath;
}

function gtChannelDetector(o){
  if ($("gt-channel") && $("gt-channel").value === "match_phase") return "match_phase";
  return o?.detector || reviewDetector();
}

function renderGtContext(o){
  const det = gtChannelDetector(o);
  const cheat = GT_CHEAT[det] || [["Real","this detector was correct"],["Not","pipeline was wrong"]];
  $("gt-cheat").innerHTML = cheat.map(([k,v])=>`<li><strong>${esc(k)}</strong> — ${esc(v)}</li>`).join("");
  renderGtRadios(det);
  const g = o ? gtAt(o.timestamp, det) : null;
  syncGtRadio(g);
  renderGtVerdict(o);
  renderGt();
  renderStats();
}

function renderGtRadios(detector){
  const labels = gtLabelsFor(detector);
  $("gt-radios").innerHTML = labels.map(l=>`<label><input type="radio" name="gt-label" value="${esc(l)}"/> ${esc(GT_LABELS[l]||l.replace(/_/g," "))}</label>`).join("");
  $("gt-radios").querySelectorAll("input").forEach(inp=>{
    inp.addEventListener("change", ()=>{
      const o=selectedObs();
      if(!o){
        alert("Select a cadence tile first, then choose a ground-truth label.");
        return;
      }
      state.gt = state.gt.filter(g => !(gtDetector(g)===gtChannelDetector(o) && o.timestamp >= g.t0 && o.timestamp <= g.t1));
      if (inp.value !== "unknown") {
        state.gt.push({t0:o.timestamp, t1:o.timestamp, detector:gtChannelDetector(o), label:inp.value});
      }
      saveGt();
    });
  });
}

function syncGtRadio(g){
  const label = g ? g.label : "unknown";
  $("gt-radios").querySelectorAll("input").forEach(inp=>{ inp.checked = inp.value===label; });
}

function exportGt(){
  const payload = {
    manifest_path: DATA.manifest_path,
    video_label: DATA.summary.video_label,
    exported_at: new Date().toISOString(),
    label_meanings: GT_LABEL_MEANINGS,
    intervals: state.gt.map(g => ({t0:g.t0, t1:g.t1, detector:gtDetector(g), label:g.label})),
  };
  const text = JSON.stringify(payload, null, 2);
  const blob = new Blob([text], {type:"application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = (DATA.summary.video_label || "vision") + "_ground_truth.json";
  a.click();
  URL.revokeObjectURL(a.href);
}

function renderGt(){
  $("gt-list").innerHTML = state.gt.length ? state.gt.map((g,i)=>`
    <div class="gt-item"><span>${g.t0===g.t1?fmtTime(g.t0, 2):`${fmtTime(g.t0, 2)}–${fmtTime(g.t1, 2)}`} · ${esc(gtDetector(g))} · <strong>${esc(GT_LABELS[g.label]||g.label)}</strong></span>
    <button type="button" data-i="${i}">remove</button></div>`).join("") : "<div style='color:var(--muted)'>No marks yet — select a cadence frame, then pick a label</div>";
  $("gt-list").querySelectorAll("button[data-i]").forEach(btn=>btn.onclick=()=>{
    state.gt.splice(parseInt(btn.getAttribute("data-i"),10),1); saveGt();
  });
}

function predictionHits(detector){
  const eventLabel = detector==="death" ? "DEATH" : detector==="splat" ? "SPLAT" : null;
  if (eventLabel) {
    const events = (DATA.markers||[]).filter(m=>
      String(m.label||"").toUpperCase()===eventLabel && String(m.id).startsWith("event:")
    );
    if (events.length) return events.map(e => e.timestamp);
  }
  return DATA.observations
    .filter(o => o.detector===detector && gtSubjectPositive(o, detector))
    .map(o => o.timestamp);
}

function renderStats(){
  const det = reviewDetector();
  const title = DETECTOR_TITLE[det] || det.replace(/_/g, " ");
  if ($("stats-title")) $("stats-title").textContent = `${title} performance (GT)`;
  const hits = predictionHits(det);
  const real = state.gt.filter(g => gtDetector(g)===det && isRealLabel(g.label, det));
  let tp=0, fn=0;
  for (const g of real){
    const hit = hits.some(ts => ts >= g.t0-1 && ts <= g.t1+1);
    if (hit) tp++; else fn++;
  }
  let fp=0;
  for (const ts of hits){
    const g = gtAt(ts, det);
    if (g && isNegativeLabel(g.label, det)) fp++;
  }
  const precision = (tp+fp)>0 ? tp/(tp+fp) : null;
  const recall = (tp+fn)>0 ? tp/(tp+fn) : null;
  const scoped = state.gt.filter(g => gtDetector(g)===det).length;
  $("stats-grid").innerHTML = `
    <div><strong>${tp}</strong><span>True positives</span></div>
    <div><strong>${fp}</strong><span>False positives</span></div>
    <div><strong>${fn}</strong><span>Misses</span></div>
    <div><strong>${precision==null?"—":(precision*100).toFixed(0)+"%" }</strong><span>Precision</span></div>
    <div><strong>${recall==null?"—":(recall*100).toFixed(0)+"%" }</strong><span>Recall</span></div>
    <div><strong>${scoped}</strong><span>GT intervals</span></div>
    <div><strong>${DATA.summary.detector_positive_event_negative||0}</strong><span>Det+/evt−</span></div>
    <div><strong>${DATA.summary.death_suppressed||0}</strong><span>Suppressed</span></div>`;
}

init();
window.addEventListener("resize",()=>{ const o=selectedObs(); if(o) updateRoi(o); });
</script>
</body>
</html>
"""
