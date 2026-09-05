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
  --accent:#7aa2f7; --death:#f7768e; --countdown:#e0af68; --life:#9ece6a;
  --active:#7dcfff; --ok:#9ece6a; --warn:#e0af68; --bad:#f7768e;
  --mono:"JetBrains Mono","SF Mono",Menlo,Consolas,monospace;
}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--text);font:13px/1.45 var(--mono)}
header,section{border-bottom:1px solid var(--line);padding:14px 18px}
h1{font-size:15px;margin:0 0 6px;font-weight:600}
h2{font-size:11px;margin:0 0 10px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
.meta{color:var(--muted);display:flex;gap:18px;flex-wrap:wrap}
.q{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:10px}
.q div{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:8px 10px}
.q strong{display:block;font-size:12px;margin-bottom:4px}
.q span{color:var(--muted);font-size:11px}
.warnings{margin-top:10px;display:grid;gap:4px}
.warnings .ok{color:var(--ok)} .warnings .warn{color:var(--warn)} .warnings .info{color:var(--accent)}
.filters{display:flex;flex-wrap:wrap;gap:12px 18px;align-items:end}
.filters label{display:grid;gap:4px;color:var(--muted);font-size:11px}
.filters select,.filters input[type=number],.filters input[type=text]{background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:4px;padding:6px 8px;font:inherit;min-width:130px}
.checks{display:flex;flex-wrap:wrap;gap:12px;align-items:center}
.checks label{display:flex;gap:6px;align-items:center;color:var(--text);font-size:12px}
button{background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:4px;padding:6px 10px;font:inherit;cursor:pointer}
button:hover,button.active{border-color:var(--accent);color:var(--accent)}
#diag-timeline,#lifecycle-strip,#detector-lanes{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:12px;overflow-x:auto}
.axis{position:relative;height:22px;border-bottom:1px solid var(--line);margin-bottom:8px;min-width:720px}
.tick{position:absolute;transform:translateX(-50%);color:var(--muted);font-size:11px}
.diag-row{display:grid;grid-template-columns:88px 1fr;gap:8px;align-items:center;margin:6px 0;min-width:720px}
.diag-track{position:relative;height:18px;border-bottom:1px solid var(--line)}
.diag-band{position:absolute;top:3px;height:10px;border-radius:2px;opacity:.85;min-width:2px}
.diag-band.death{background:var(--death)}.diag-band.countdown{background:var(--countdown)}.diag-band.active{background:var(--active)}
.diag-band.alive{background:var(--life)}.diag-band.dead{background:var(--death)}.diag-band.respawned{background:#73daca}.diag-band.unknown{background:#565f89}
.evt-mark{position:absolute;top:-2px;transform:translateX(-50%);cursor:pointer;font-size:11px;line-height:1;color:var(--death)}
.evt-mark.respawn{color:var(--countdown)}.evt-mark.active_again{color:var(--active)}
.life-row{position:relative;min-width:720px;margin-top:8px}
.life-seg{position:absolute;top:8px;height:14px;border-radius:3px;opacity:.9;min-width:2px}
.life-seg.unknown{background:#565f89}.life-seg.alive{background:var(--life)}.life-seg.dead{background:var(--death)}
.life-seg.countdown{background:var(--countdown)}.life-seg.respawned{background:#73daca}
.life-mark{position:absolute;transform:translateX(-50%);text-align:center;cursor:pointer;z-index:1;max-width:110px}
.life-mark:hover,.life-mark.selected{z-index:5}
.life-mark .dot{width:10px;height:10px;border-radius:50%;background:var(--accent);margin:0 auto;border:1px solid #000}
.life-mark.selected .dot{outline:2px solid #fff;outline-offset:2px}
.life-mark .stem{width:1px;height:8px;background:var(--line);margin:0 auto}
.life-mark .lbl{font-size:10px;line-height:1.2;padding:2px 4px;border-radius:3px;background:rgba(20,22,26,.92);border:1px solid var(--line);white-space:normal;word-break:break-word}
.life-mark .t{color:var(--muted)}.life-mark .e{color:var(--text)}
.life-mark.compact .e{font-size:9px}
.lane-row{display:grid;grid-template-columns:90px 1fr;gap:8px;align-items:center;margin:8px 0;min-width:720px}
.lane-track{position:relative;height:22px;border-bottom:1px solid var(--line)}
.sample{position:absolute;top:4px;width:10px;height:10px;border-radius:50%;transform:translateX(-50%);cursor:pointer;border:1px solid #000}
.sample.on{background:#fff}.sample.off{background:transparent;border:2px solid #fff}
.sample.death.on{background:var(--death)}.sample.countdown.on{background:var(--countdown)}.sample.active_gameplay.on{background:var(--active)}
.sample.suppressed{outline:2px solid var(--warn);outline-offset:1px}
.gallery{display:flex;gap:10px;overflow-x:auto;padding-bottom:6px}
.card{flex:0 0 132px;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:8px;cursor:pointer}
.card.no-thumb{flex-basis:78px;max-width:78px;padding:6px}
.card.selected{border-color:var(--accent)}
.card.flag-dpen{border-color:var(--warn)}
.card img{width:100%;height:72px;object-fit:cover;background:#0b0d10;border:1px dashed var(--line);border-radius:4px;display:block}
.card .ph,.frame-stage .ph{min-height:72px;display:grid;place-items:center;color:var(--muted);border:1px dashed var(--line);border-radius:4px;padding:8px;text-align:center;font-size:10px;line-height:1.35}
.card.no-thumb .ph{min-height:36px;padding:4px 2px;font-size:9px}
.frame-stage .ph{min-height:120px;font-size:11px}
.card small{display:block;color:var(--muted);margin-top:4px}
.card.no-thumb small{font-size:9px;margin-top:2px;overflow:hidden;text-overflow:ellipsis}
.nav-row{display:flex;gap:8px;margin-bottom:10px}
.detail{display:grid;grid-template-columns:minmax(240px,400px) 1fr;gap:16px}
.frame-preview{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:8px}
.frame-stage{position:relative}
.frame-preview img{width:100%;max-height:300px;object-fit:contain;display:block;background:#0b0d10;border-radius:4px}
.roi-box{position:absolute;border:2px solid var(--countdown);pointer-events:none;box-shadow:0 0 0 1px #000}
.layers{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin:10px 0}
.layer{background:#0b0d10;border:1px solid var(--line);border-radius:6px;padding:8px}
.layer h3{margin:0 0 6px;font-size:11px;color:var(--muted);text-transform:uppercase}
.kv{display:grid;grid-template-columns:1fr auto;gap:3px 10px}
.kv span:nth-child(odd){color:var(--muted)}
.causal,.diag-panel,.why,.gt-panel,.stats-panel{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:10px;margin-top:10px}
.causal.bad{border-color:var(--bad)}.causal.warn{border-color:var(--warn)}.causal.ok{border-color:var(--ok)}
.causal h3,.diag-panel h3,.why h3,.gt-panel h3{margin:0 0 8px;font-size:13px}
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
.episode.incomplete{border-color:var(--warn)}
.comp{display:flex;gap:12px;flex-wrap:wrap;margin:6px 0 10px}
.comp .c{font-size:11px}.comp .c.ok{color:var(--ok)}.comp .c.bad{color:var(--bad)}
.latch{margin:8px 0;padding:8px;background:#0b0d10;border-radius:6px}
.latch-row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.latch-item{text-align:center;min-width:70px}
.latch-item .v{font-weight:700}.latch-item .v.t{color:var(--ok)}.latch-item .v.f{color:var(--muted)}
.step{display:grid;grid-template-columns:70px 130px 1fr;gap:8px;padding:3px 0;border-left:2px solid var(--line);padding-left:10px;margin-left:4px;cursor:pointer}
.step:hover{border-left-color:var(--accent)}
.near{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:8px 10px;margin:6px 0;cursor:pointer}
.near:hover{border-color:var(--accent)}
.gt-radios{display:flex;flex-wrap:wrap;gap:8px 14px;margin:8px 0}
.gt-radios label{display:flex;gap:6px;align-items:center;font-size:12px}
.gt-list{margin-top:8px;display:grid;gap:4px}
.gt-item{display:flex;justify-content:space-between;gap:8px;padding:4px 0;border-bottom:1px solid var(--line)}
.stats-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.stats-grid div{background:#0b0d10;border-radius:6px;padding:8px;text-align:center}
.stats-grid strong{display:block;font-size:18px}
.stats-grid span{color:var(--muted);font-size:11px}
@media (max-width:900px){.detail,.q,.layers,.stats-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <h1>VISION MANIFEST VIEWER · DIAGNOSTIC</h1>
  <div class="meta" id="header-meta"></div>
  <div class="q" id="three-q"></div>
  <div class="warnings" id="warnings"></div>
</header>

<section>
  <h2>Filters</h2>
  <div class="filters">
    <label>Detector<select id="filter-detector"></select></label>
    <label>Category<select id="filter-category"></select></label>
    <label>Confidence ≥<input id="filter-conf" type="number" min="0" max="1" step="0.05" value="0.50"/></label>
    <label>Time start<input id="filter-t0" type="number" min="0" step="0.1" value="0"/></label>
    <label>Time end<input id="filter-t1" type="number" min="0" step="0.1" value="0"/></label>
    <div class="checks">
      <label><input type="checkbox" id="filter-lifecycle"/> Only lifecycle-related</label>
      <label><input type="checkbox" id="filter-images"/> Only frames with images</label>
      <label><input type="checkbox" id="filter-positive" checked/> Only positive detections</label>
      <label><input type="checkbox" id="filter-near"/> Near misses</label>
      <label><input type="checkbox" id="filter-dpen"/> Detector+/event−</label>
      <label><input type="checkbox" id="filter-near-thr"/> Near threshold</label>
      <label><input type="checkbox" id="filter-fp"/> False positives (GT)</label>
      <label><input type="checkbox" id="filter-miss"/> Missed deaths (GT)</label>
      <label><input type="checkbox" id="filter-weak-ouch"/> Weak Ouch</label>
      <button type="button" id="btn-episodes">Show death episodes</button>
    </div>
  </div>
  <div id="filter-status" style="margin-top:10px;color:var(--muted);font-size:12px"></div>
</section>

<section>
  <h2>Diagnostic timeline</h2>
  <div id="diag-timeline"></div>
</section>

<section>
  <h2>Lifecycle</h2>
  <div id="lifecycle-strip"></div>
</section>

<section>
  <h2>Detector timeline</h2>
  <div id="detector-lanes"></div>
</section>

<section>
  <h2>Evidence</h2>
  <div class="nav-row">
    <button type="button" id="btn-gal-earlier">« Earlier</button>
    <button type="button" id="btn-prev">← Previous</button>
    <button type="button" id="btn-next">Next →</button>
    <button type="button" id="btn-gal-later">Later »</button>
    <span id="gallery-window" style="color:var(--muted);align-self:center;font-size:11px"></span>
  </div>
  <div class="gallery" id="gallery"></div>
</section>

<section id="near-section" hidden>
  <h2>Near misses / diagnostics</h2>
  <div id="near-misses"></div>
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
      <div class="gt-panel" id="gt-panel">
        <h3>Ground truth</h3>
        <p style="color:var(--muted);margin:0 0 8px;font-size:11px;line-height:1.4">
          Labels are stored in <strong>this browser only</strong> (localStorage). They drive the
          FP/Miss filters and the stats below — they do <strong>not</strong> retrain detectors yet.
          Export JSON if you want to keep them for calibration.
        </p>
        <p style="color:var(--muted);margin:0 0 8px;font-size:11px;line-height:1.4">
          <strong>Current frame:</strong> pick a radio to label the selected observation (~0.5s window).<br/>
          <strong>Time range:</strong> set start/end, pick a radio, then click <em>Mark interval</em>.
        </p>
        <div class="gt-radios" id="gt-radios"></div>
        <div class="filters" style="margin-top:8px">
          <label>Interval start<input id="gt-t0" type="number" step="0.1"/></label>
          <label>Interval end<input id="gt-t1" type="number" step="0.1"/></label>
          <button type="button" id="btn-gt-add">Mark interval</button>
          <button type="button" id="btn-gt-export">Export GT JSON</button>
          <button type="button" id="btn-gt-clear">Clear all GT</button>
        </div>
        <div class="gt-list" id="gt-list"></div>
      </div>
      <div class="stats-panel" id="stats-panel">
        <h3>Death detection performance (GT)</h3>
        <div class="stats-grid" id="stats-grid"></div>
      </div>
    </div>
    <div>
      <div class="kv" id="obs-meta"></div>
      <div class="layers" id="layers"></div>
      <div class="causal" id="causal" hidden></div>
      <div class="diag-panel" id="diag-panel" hidden></div>
      <div id="reading"></div>
      <div class="why" id="why" hidden></div>
      <pre class="raw" id="raw-json"></pre>
    </div>
  </div>
</section>

<section id="episodes-section" hidden>
  <h2>Death episodes</h2>
  <div class="episodes" id="episodes"></div>
</section>

<script id="viewer-data" type="application/json">__DATA_JSON__</script>
<script>
const DATA = JSON.parse(document.getElementById("viewer-data").textContent);
const GT_KEY = "vmv-gt:" + (DATA.manifest_path || DATA.summary.video_identity || "default");
const state = {
  selectedId:null, selectedTransitionId:null, showRoi:false, showRaw:false, filtered:[],
  gt: loadGt(),
  galleryOffset: 0,  // index into filtered detector list for gallery window start
};
const GALLERY_PAGE = 200;
const $ = id => document.getElementById(id);
const esc = s => String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
const num = v => (v==null||Number.isNaN(Number(v))) ? "—" : Number(v).toFixed(3);
const GT_LABELS = {
  unknown:"Unknown", real_death:"Real death", not_a_death:"Not a death",
  damage_recovery:"Damage/recovery", results_screen:"Results screen", lobby:"Lobby"
};

function loadGt(){
  try { return JSON.parse(localStorage.getItem(GT_KEY)||"[]"); } catch { return []; }
}
function saveGt(){ localStorage.setItem(GT_KEY, JSON.stringify(state.gt)); renderGt(); renderStats(); refresh(); }

function init() {
  $("filter-detector").innerHTML = '<option value="">All</option>'+DATA.detectors.map(d=>`<option value="${esc(d)}">${esc(d)}</option>`).join("");
  $("filter-category").innerHTML = '<option value="">All</option>'+DATA.categories.map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join("");
  $("filter-t1").value = DATA.summary.duration_seconds.toFixed(1);
  const filterIds = ["filter-category","filter-conf","filter-t0","filter-t1","filter-lifecycle","filter-images","filter-positive","filter-near","filter-dpen","filter-near-thr","filter-fp","filter-miss","filter-weak-ouch"];
  for (const id of filterIds) {
    $(id).addEventListener("input", refresh); $(id).addEventListener("change", refresh);
  }
  // Selecting a detector: show all readings for it (positives + negatives),
  // otherwise "Only positive" hides most active_gameplay/countdown frames.
  $("filter-detector").addEventListener("change", () => {
    if ($("filter-detector").value) $("filter-positive").checked = false;
    refresh();
  });
  $("btn-prev").onclick = () => step(-1);
  $("btn-next").onclick = () => step(1);
  $("btn-gal-earlier").onclick = () => shiftGallery(-GALLERY_PAGE);
  $("btn-gal-later").onclick = () => shiftGallery(GALLERY_PAGE);
  $("btn-roi").onclick = () => { state.showRoi=!state.showRoi; renderDetail(); };
  $("btn-raw").onclick = () => { state.showRaw=!state.showRaw; renderDetail(); };
  $("btn-episodes").onclick = () => {
    const sec=$("episodes-section"); sec.hidden=!sec.hidden;
    $("btn-episodes").classList.toggle("active", !sec.hidden);
    if (!sec.hidden) renderEpisodes();
  };
  $("btn-gt-add").onclick = addGtInterval;
  $("btn-gt-export").onclick = exportGt;
  $("btn-gt-clear").onclick = () => { if(confirm("Clear all ground-truth marks for this manifest?")){ state.gt=[]; saveGt(); } };
  renderGtRadios();
  refresh();
  renderGt();
  renderStats();
}

function diagFor(o){
  if (!o) return null;
  if (o.diagnostic_id) return (DATA.death_diagnostics||[]).find(d=>d.id===o.diagnostic_id)||null;
  return (DATA.death_diagnostics||[]).find(d=>d.observation_id===o.id)||null;
}

function gtAt(ts){
  const hits = state.gt.filter(g => ts >= g.t0 && ts <= g.t1);
  return hits.length ? hits[hits.length-1] : null;
}

function isFalsePositiveObs(o){
  if (o.detector !== "death" || !o.positive) return false;
  const g = gtAt(o.timestamp);
  if (!g) return false;
  return ["not_a_death","damage_recovery","results_screen","lobby"].includes(g.label);
}

function isMissedDeathObs(o){
  // Missed deaths are GT real_death intervals with no DEATH event — filter shows death obs in those windows that were rejected/suppressed, OR any obs in window
  const g = gtAt(o.timestamp);
  if (!g || g.label !== "real_death") return false;
  const events = (DATA.summary.event_counts && Object.keys(DATA.summary.event_counts)) || [];
  const deathEvents = (DATA.markers||[]).filter(m => m.label === "DEATH" && !String(m.id).startsWith("obs:"));
  const hasEvent = deathEvents.some(m => Math.abs(m.timestamp - o.timestamp) <= 2.0)
    || (DATA.death_diagnostics||[]).some(d => d.event_emitted && Math.abs(d.timestamp - o.timestamp) <= 2.0 && d.decision==="confirmed");
  // For gallery: show death observations inside a GT real_death window that did not confirm
  if (o.detector === "death") {
    const d = diagFor(o);
    return !d || d.decision !== "confirmed";
  }
  return !hasEvent;
}

function passes(o) {
  const det=$("filter-detector").value, cat=$("filter-category").value;
  const conf=parseFloat($("filter-conf").value||"0");
  const t0=parseFloat($("filter-t0").value||"0"), t1=parseFloat($("filter-t1").value||"1e9");
  if (det && o.detector!==det) return false;
  if (cat && o.category!==cat) return false;
  if (o.timestamp<t0 || o.timestamp>t1) return false;
  if ($("filter-images").checked && !o.image_relpath) return false;
  const dpen = $("filter-dpen").checked;
  const nearThr = $("filter-near-thr").checked;
  const weak = $("filter-weak-ouch").checked;
  const fp = $("filter-fp").checked;
  const miss = $("filter-miss").checked;
  const special = dpen || nearThr || weak || fp || miss;
  if ($("filter-positive").checked && o.detector && !o.positive && !special) return false;
  if (o.confidence!=null && o.confidence<conf && $("filter-positive").checked && !special) return false;
  if ($("filter-lifecycle").checked) {
    const life=new Set(["death","countdown","respawn","active_gameplay","lifecycle"]);
    if (!life.has(o.category)) return false;
  }
  const flags = new Set(o.diagnostic_flags||[]);
  if (dpen && !flags.has("detector_positive_event_negative")) return false;
  if (nearThr && !flags.has("near_threshold")) return false;
  if (weak && !flags.has("weak_ouch")) return false;
  if (fp && !isFalsePositiveObs(o)) return false;
  if (miss && !isMissedDeathObs(o)) return false;
  return true;
}

function refresh() {
  state.filtered = DATA.observations.filter(passes);
  if (!state.selectedId || !state.filtered.some(o=>o.id===state.selectedId))
    state.selectedId = state.filtered.find(o=>o.detector)?.id || state.filtered[0]?.id || null;
  // Keep gallery window covering the selection (fixes hard cut at ~15s / first 200).
  syncGalleryToSelection();
  const o = selectedObs();
  if (o?.transition_id) state.selectedTransitionId = o.transition_id;
  renderHeader(); renderFilterStatus(); renderDiagTimeline(); renderLifecycle(); renderDetectors(); renderGallery(); renderNear(); renderDetail();
}

function filteredDetectors() {
  return state.filtered.filter(o => o.detector);
}

function syncGalleryToSelection() {
  const all = filteredDetectors();
  if (!all.length) { state.galleryOffset = 0; return; }
  const idx = all.findIndex(o => o.id === state.selectedId);
  if (idx < 0) {
    state.galleryOffset = Math.min(state.galleryOffset, Math.max(0, all.length - GALLERY_PAGE));
    return;
  }
  if (idx < state.galleryOffset || idx >= state.galleryOffset + GALLERY_PAGE) {
    state.galleryOffset = Math.max(0, Math.min(idx - Math.floor(GALLERY_PAGE / 2), all.length - GALLERY_PAGE));
  }
  state.galleryOffset = Math.max(0, state.galleryOffset);
}

function shiftGallery(delta) {
  const all = filteredDetectors();
  if (!all.length) return;
  const maxOff = Math.max(0, all.length - GALLERY_PAGE);
  state.galleryOffset = Math.max(0, Math.min(maxOff, state.galleryOffset + delta));
  // Select first card in the new window so detail stays in sync.
  const card = all[state.galleryOffset];
  if (card) {
    state.selectedId = card.id;
    if (card.transition_id) state.selectedTransitionId = card.transition_id;
  }
  renderGallery(); renderDetail(); renderFilterStatus();
}

function renderFilterStatus() {
  const det=$("filter-detector").value;
  const n=filteredDetectors().length;
  const total=DATA.observations.filter(o=>o.detector).length;
  const pos=state.filtered.filter(o=>o.detector && o.positive).length;
  const bits=[];
  if (det) bits.push(`detector=${det}`);
  if ($("filter-positive").checked) bits.push("positives only");
  else bits.push("positives + negatives");
  bits.push(`time ${$("filter-t0").value}–${$("filter-t1").value}s`);
  const win = galleryWindowBounds();
  $("filter-status").innerHTML =
    `<strong>Evidence gallery:</strong> <strong>${n}</strong> matching of ${total} detector observations` +
    ` (${pos} positive) · ${bits.join(" · ")}` +
    (n===0 ? ` <span style="color:var(--bad)">— nothing matches; uncheck “Only positive” or widen time</span>` : "") +
    (win ? `<br/><span>Gallery page: cards ${win.start+1}–${win.end} · ${win.t0.toFixed(1)}s–${win.t1.toFixed(1)}s (use Later » / time filter for the rest of the video)</span>` : "") +
    `<br/><span style="opacity:.85">Timelines above stay full-video context; filters apply to Evidence gallery, prev/next, and detector-lane emphasis.</span>`;
}

function galleryWindowBounds() {
  const all = filteredDetectors();
  if (!all.length) return null;
  const start = state.galleryOffset;
  const end = Math.min(all.length, start + GALLERY_PAGE);
  return { start, end, t0: all[start].timestamp, t1: all[end-1].timestamp };
}

function renderGallery() {
  const all = filteredDetectors();
  const start = Math.max(0, Math.min(state.galleryOffset, Math.max(0, all.length - 1)));
  const cards = all.slice(start, start + GALLERY_PAGE);
  const win = galleryWindowBounds();
  $("gallery-window").textContent = win
    ? `${win.start+1}–${win.end} / ${all.length} · ${win.t0.toFixed(1)}–${win.t1.toFixed(1)}s`
    : "";
  $("gallery").innerHTML = cards.map(o=>{
    const sel=o.id===state.selectedId?"selected":"";
    const flags=new Set(o.diagnostic_flags||[]);
    const flagCls=flags.has("detector_positive_event_negative")?"flag-dpen":"";
    const noThumb = !o.image_relpath;
    const thumbCls = noThumb ? "no-thumb" : "";
    const img = o.image_relpath
      ? `<img src="${esc(o.image_relpath)}" loading="lazy"/>`
      : `<div class="ph">${o.source==="cadence"?"cadence<br/>no JPEG":"no<br/>image"}</div>`;
    const badge = flags.has("detector_positive_event_negative") ? "det+/evt−" : (flags.has("weak_ouch")?"weak ouch":"");
    const pol = o.positive ? "+" : "−";
    return `<div class="card ${sel} ${flagCls} ${thumbCls}" data-oid="${esc(o.id)}">${img}<small>${o.timestamp.toFixed(2)}s · ${pol}</small><small>#${o.frame_index??"—"}</small><small>${esc((o.detector||"").toUpperCase())}</small><small>${o.confidence!=null?o.confidence.toFixed(2):"—"}</small>${badge?`<small style="color:var(--warn)">${badge}</small>`:""}</div>`;
  }).join("") || "<div class='ph'>No evidence matches filters</div>";
  $("gallery").querySelectorAll(".card").forEach(el=>el.onclick=()=>{
    state.selectedId=el.getAttribute("data-oid");
    const o=selectedObs(); if(o?.transition_id) state.selectedTransitionId=o.transition_id;
    renderGallery(); renderDetail(); renderLifecycle(); renderDiagTimeline();
  });
  // Keep the selected card visible horizontally when possible.
  const selectedCard = $("gallery").querySelector(".card.selected");
  if (selectedCard) selectedCard.scrollIntoView({inline:"nearest", block:"nearest", behavior:"smooth"});
}

function renderHeader() {
  const s=DATA.summary;
  $("header-meta").innerHTML = `<span>${esc(s.video_label)}</span><span>${s.duration_seconds.toFixed(1)}s</span><span>${s.frame_count} frames</span><span>det+/evt− ${s.detector_positive_event_negative||0}</span>`;
  $("three-q").innerHTML = `
    <div><strong>1. What did detectors see?</strong><span>${s.detection_count} detections · death ${s.death_detections} · countdown ${s.countdown_observations} · active ${s.active_observations}</span></div>
    <div><strong>2. What did lifecycle conclude?</strong><span>${s.lifecycle_episodes} episodes · events ${Object.entries(s.event_counts||{}).map(([k,v])=>k+":"+v).join(" · ")||"—"} · confirmed ${s.death_confirmed||0} · suppressed ${s.death_suppressed||0}</span></div>
    <div><strong>3. Why that decision?</strong><span>${(DATA.death_diagnostics||[]).length} death diagnostics · ${(DATA.transitions||[]).length} lifecycle transitions</span></div>`;
  $("warnings").innerHTML = (s.warnings||[]).map(w=>`<div class="${w.level}">${w.level==="ok"?"✓":"⚠"} ${esc(w.message)}</div>`).join("");
}

function renderDiagTimeline() {
  const duration=Math.max(DATA.summary.duration_seconds,1);
  const width=Math.max(720, Math.ceil(duration*8));
  const ticks=[];
  const step=duration>180?30:duration>60?20:10;
  for (let t=0;t<=duration+0.01;t+=step) ticks.push(`<div class="tick" style="left:${(t/duration)*100}%">${Math.round(t)}s</div>`);

  const deathLane = (DATA.detector_lanes||[]).find(l=>l.detector==="death");
  const cdLane = (DATA.detector_lanes||[]).find(l=>l.detector==="respawn"||l.detector==="countdown");
  const actLane = (DATA.detector_lanes||[]).find(l=>l.detector==="active_gameplay");

  function dots(lane, cls){
    if (!lane) return "";
    return lane.samples.filter(s=>s.positive).map(s=>{
      const left=(s.timestamp/duration)*100;
      const diag=(DATA.death_diagnostics||[]).find(d=>d.observation_id===s.observation_id);
      const suppressed = diag && diag.decision==="suppressed";
      return `<div class="sample ${cls} on ${suppressed?"suppressed":""}" style="left:${left}%" title="${s.timestamp.toFixed(2)}s" data-oid="${esc(s.observation_id||"")}"></div>`;
    }).join("");
  }

  const lifeBands = (DATA.lifecycle_segments||[]).map(seg=>{
    const left=(seg.start/duration)*100, w=Math.max(((seg.end-seg.start)/duration)*100,0.15);
    return `<div class="diag-band ${esc(seg.phase)}" style="left:${left}%;width:${w}%" title="${esc(seg.phase)}"></div>`;
  }).join("");

  const eventMarks = (DATA.markers||[]).filter(m=>["DEATH","RESPAWN","ACTIVE AGAIN"].includes(m.label)).map(m=>{
    const left=(m.timestamp/duration)*100;
    const cls = m.label==="DEATH"?"death":(m.label==="RESPAWN"?"respawn":"active_again");
    return `<div class="evt-mark ${cls}" style="left:${left}%" title="${esc(m.label)} ${m.timestamp.toFixed(2)}s" data-ts="${m.timestamp}" data-tid="${esc(m.transition_id||"")}">▲</div>`;
  }).join("");

  $("diag-timeline").innerHTML = `<div style="min-width:${width}px">
    <div class="axis">${ticks.join("")}</div>
    <div class="diag-row"><div>DEATH</div><div class="diag-track">${dots(deathLane,"death")}</div></div>
    <div class="diag-row"><div>COUNTDOWN</div><div class="diag-track">${dots(cdLane,"countdown")}</div></div>
    <div class="diag-row"><div>ACTIVE</div><div class="diag-track">${dots(actLane,"active_gameplay")}</div></div>
    <div class="diag-row"><div>LIFECYCLE</div><div class="diag-track">${lifeBands}</div></div>
    <div class="diag-row"><div>EVENTS</div><div class="diag-track">${eventMarks}</div></div>
  </div>`;
  $("diag-timeline").querySelectorAll(".sample[data-oid],.evt-mark").forEach(el=>{
    el.onclick=()=>{
      const oid=el.getAttribute("data-oid");
      const tid=el.getAttribute("data-tid");
      const ts=parseFloat(el.getAttribute("data-ts"));
      if (tid) state.selectedTransitionId=tid;
      if (oid) state.selectedId=oid;
      else if (!Number.isNaN(ts)) {
        const near=DATA.observations.filter(o=>o.detector==="death").sort((a,b)=>Math.abs(a.timestamp-ts)-Math.abs(b.timestamp-ts))[0];
        if (near) state.selectedId=near.id;
      }
      renderGallery(); renderDetail(); renderLifecycle(); renderDiagTimeline();
    };
  });
}

function renderLifecycle() {
  const duration=Math.max(DATA.summary.duration_seconds,1);
  const width=Math.max(720, Math.ceil(duration*8));
  const ticks=[];
  const step=duration>180?30:duration>60?20:10;
  for (let t=0;t<=duration+0.01;t+=step) ticks.push(`<div class="tick" style="left:${(t/duration)*100}%">${Math.round(t)}s</div>`);
  const segs=(DATA.lifecycle_segments||[]).map(seg=>{
    const left=(seg.start/duration)*100, w=Math.max(((seg.end-seg.start)/duration)*100,0.15);
    return `<div class="life-seg ${esc(seg.phase)}" style="left:${left}%;width:${w}%" title="${esc(seg.phase)}"></div>`;
  }).join("");

  // Stagger labels that would collide when transitions are close in time.
  const minGapPx = 96;
  const laneTops = []; // last x-pixel occupied per lane
  const placed = (DATA.lifecycle_marks||[]).slice().sort((a,b)=>a.timestamp-b.timestamp).map(m=>{
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
    const tip = `${m.timestamp.toFixed(2)}s · ${m.event_label} → ${(m.phase_label||"").toUpperCase()}`;
    const top = 26 + lane * 44;
    return `<div class="life-mark ${sel} ${compact}" style="left:${leftPct}%;top:${top}px" title="${esc(tip)}" data-tid="${esc(m.transition_id||"")}" data-ts="${m.timestamp}">
      <div class="dot"></div><div class="stem"></div>
      <div class="lbl"><div class="t">${m.timestamp.toFixed(1)}s</div><div class="e">${esc(m.event_label)}</div></div>
    </div>`;
  }).join("");

  $("lifecycle-strip").innerHTML = `<div style="min-width:${width}px"><div class="axis">${ticks.join("")}</div><div class="life-row" style="height:${rowH}px">${segs}${marks}</div></div>`;
  $("lifecycle-strip").querySelectorAll(".life-mark").forEach(el=>{
    el.onclick=()=>{
      const tid=el.getAttribute("data-tid");
      const ts=parseFloat(el.getAttribute("data-ts"));
      if (tid) state.selectedTransitionId=tid;
      const tr=(DATA.transitions||[]).find(t=>t.id===tid);
      if (tr?.observation_id) state.selectedId=tr.observation_id;
      else {
        const near=state.filtered.filter(o=>o.detector).sort((a,b)=>Math.abs(a.timestamp-ts)-Math.abs(b.timestamp-ts))[0];
        if (near) state.selectedId=near.id;
      }
      renderLifecycle(); renderGallery(); renderDetail(); renderDiagTimeline();
    };
  });
}

function renderDetectors() {
  const duration=Math.max(DATA.summary.duration_seconds,1);
  const t0=parseFloat($("filter-t0").value||"0"), t1=parseFloat($("filter-t1").value||"1e9");
  const focus=$("filter-detector").value;
  const rows=(DATA.detector_lanes||[]).filter(lane=>!focus || lane.detector===focus).map(lane=>{
    const samples=lane.samples.filter(s=>s.timestamp>=t0 && s.timestamp<=t1).map(s=>{
      const left=(s.timestamp/duration)*100;
      const cls=s.positive?"on":"off";
      const diag=(DATA.death_diagnostics||[]).find(d=>d.observation_id===s.observation_id);
      const suppressed = diag && diag.decision==="suppressed" ? "suppressed" : "";
      return `<div class="sample ${esc(lane.detector)} ${cls} ${suppressed}" style="left:${left}%" title="${lane.label} ${s.timestamp.toFixed(2)}s ${s.positive?"+":"-"}" data-oid="${esc(s.observation_id||"")}"></div>`;
    }).join("");
    return `<div class="lane-row"><div>${esc(lane.label)}</div><div class="lane-track">${samples}</div></div>`;
  }).join("");
  $("detector-lanes").innerHTML = rows || "<div class='ph'>No detector lanes</div>";
  $("detector-lanes").querySelectorAll(".sample[data-oid]").forEach(el=>{
    el.onclick=()=>{ const oid=el.getAttribute("data-oid"); if(!oid) return; state.selectedId=oid;
      const o=DATA.observations.find(x=>x.id===oid); if(o?.transition_id) state.selectedTransitionId=o.transition_id;
      syncGalleryToSelection();
      renderGallery(); renderDetail(); renderLifecycle(); renderDiagTimeline(); renderFilterStatus(); };
  });
}

function renderNear() {
  const show=$("filter-near").checked || $("filter-dpen").checked || $("filter-near-thr").checked || $("filter-weak-ouch").checked;
  $("near-section").hidden=!show;
  if (!show) return;
  let items=DATA.near_misses||[];
  if ($("filter-dpen").checked) items=items.filter(m=>m.kind==="detector_positive_event_negative");
  else if ($("filter-near-thr").checked) items=items.filter(m=>m.kind==="near_threshold");
  else if ($("filter-weak-ouch").checked) items=items.filter(m=>m.kind==="weak_ouch");
  $("near-misses").innerHTML = items.map(m=>`<div class="near" data-oid="${esc(m.observation_id||"")}"><strong>${m.timestamp.toFixed(2)}s</strong> · ${esc(m.kind)} · ${esc(m.summary)}<div style="color:var(--muted)">${esc(m.detail)}</div></div>`).join("") || "<div class='ph'>No near misses</div>";
  $("near-misses").querySelectorAll(".near[data-oid]").forEach(el=>el.onclick=()=>{
    const oid=el.getAttribute("data-oid"); if(!oid) return; state.selectedId=oid; renderGallery(); renderDetail();
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
  const list=filteredDetectors(); if(!list.length) return;
  let idx=list.findIndex(o=>o.id===state.selectedId); if(idx<0) idx=0;
  const next=list[(idx+dir+list.length)%list.length];
  state.selectedId=next.id; if(next.transition_id) state.selectedTransitionId=next.transition_id;
  syncGalleryToSelection();
  renderGallery(); renderDetail(); renderLifecycle(); renderDiagTimeline(); renderFilterStatus();
}

function renderDetail(){
  const o=selectedObs(); const tr=selectedTransition(); const diag=diagFor(o);
  const img=$("frame-img"), ph=$("frame-ph"), overlay=$("roi-overlay");
  if (!o){ ph.hidden=false; img.hidden=true; overlay.hidden=true; $("obs-meta").innerHTML=""; $("reading").innerHTML=""; $("why").hidden=true; $("causal").hidden=true; $("diag-panel").hidden=true; return; }
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
  const g = gtAt(o.timestamp);
  $("obs-meta").innerHTML = `
    <span>Timestamp</span><strong>${o.timestamp.toFixed(3)}s</strong>
    <span>Frame</span><strong>${o.frame_index??"—"}</strong>
    <span>Source</span><strong>${esc(o.source||"—")}</strong>
    <span>Reading</span><strong>${esc(o.reading_kind||o.detector||"—")}</strong>
    <span>Confidence</span><strong>${num(o.confidence)}</strong>
    <span>Image</span><strong>${o.image_relpath?esc(o.image_relpath):(o.source==="cadence"?"(cadence — not persisted)":"—")}</strong>
    <span>Ground truth</span><strong>${esc(g?GT_LABELS[g.label]||g.label:"Unknown")}</strong>`;
  $("gt-t0").value = o.timestamp.toFixed(1);
  $("gt-t1").value = (o.timestamp+1).toFixed(1);
  $("layers").innerHTML = `
    <div class="layer"><h3>1. Raw observation</h3><div class="kv">
      <span>detector</span><strong>${esc(o.detector||"—")}</strong>
      <span>positive</span><strong>${o.positive}</strong>
      <span>score</span><strong>${num(o.reading.presence_score??o.reading.score??o.confidence)}</strong>
    </div></div>
    <div class="layer"><h3>2. Fused state</h3><div class="kv">
      <span>player_lifecycle</span><strong>${esc(o.lifecycle||"—")}</strong>
      <span>countdown_present</span><strong>${o.countdown_present??"—"}</strong>
      <span>active_gameplay</span><strong>${o.active_gameplay??"—"}</strong>
      <span>player_alive</span><strong>${o.player_alive??"—"}</strong>
      <span>latch</span><strong>${o.latch??"—"}</strong>
    </div></div>
    <div class="layer"><h3>3. Events</h3><div class="kv">
      <span>nearby</span><strong>${esc(tr?.title||"—")}</strong>
      <span>transition</span><strong>${esc((tr?.from_phase||"?")+" → "+(tr?.to_phase||"?"))}</strong>
      <span>death event</span><strong>${diag?(diag.event_emitted?"yes @ "+num(diag.event_timestamp):"no"):"—"}</strong>
    </div></div>`;
  renderCausal(diag);
  renderDeathDiag(diag, o);
  $("reading").innerHTML = renderReading(o);
  renderWhy(tr, diag, o);
  const raw=$("raw-json"); raw.textContent=JSON.stringify({observation:o, transition:tr, diagnostic:diag}, null, 2);
  raw.classList.toggle("open", state.showRaw);
  $("btn-raw").classList.toggle("active", state.showRaw);
  $("btn-roi").classList.toggle("active", state.showRoi);
  $("roi-panel").classList.toggle("open", state.showRoi && !!o.roi);
  if (state.showRoi && o.roi) {
    $("roi-kv").innerHTML=`<span>x1</span><strong>${o.roi.x1.toFixed(3)}</strong><span>y1</span><strong>${o.roi.y1.toFixed(3)}</strong><span>x2</span><strong>${o.roi.x2.toFixed(3)}</strong><span>y2</span><strong>${o.roi.y2.toFixed(3)}</strong>`;
    drawRoi(o);
  }
  updateRoi(o);
  syncGtRadio(g);
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

function renderWhy(tr, diag, o){
  const box=$("why");
  // Prefer death diagnostic rule panel when on a death obs without a useful transition
  if (diag && o && o.detector==="death" && (!tr || Math.abs((tr.timestamp||0)-o.timestamp)>2)) {
    box.hidden=true;
    return;
  }
  if (!tr){ box.hidden=true; return; }
  box.hidden=false; box.classList.toggle("anomaly", !!tr.anomaly);
  const checks=(tr.checks||[]).map(c=>`<div class="check ${c.ok?"ok":"bad"}">${c.ok?"✓":"✗"} ${esc(c.label)}</div>`).join("");
  box.innerHTML = `<h3>Why? ${esc(tr.title)}</h3>
    <div><strong>${esc(tr.from_phase||"?")} → ${esc(tr.to_phase||"?")}</strong> · ${tr.timestamp.toFixed(2)}s</div>
    <div style="margin-top:8px;color:var(--muted)">Required evidence</div>
    ${checks}
    <div class="kv" style="margin-top:8px"><span>player_alive</span><strong>${tr.player_alive??"—"}</strong><span>latch</span><strong>${tr.latch??"—"}</strong></div>
    <div class="meaning">${esc(tr.meaning||"")}</div>
    ${tr.anomaly_message?`<div class="check bad">⚠ ${esc(tr.anomaly_message)}</div>`:""}`;
}

function renderReading(o){
  const r=o.reading||{};
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
  if (o.detector==="active_gameplay") return `<h2>ActiveGameplay reading</h2><div class="kv"><span>detected</span><strong>${r.detected}</strong><span>score</span><strong>${num(r.score)}</strong><span>weapon edge</span><strong>${num(r.weapon_edge_frac)}</strong><span>hud edge</span><strong>${num(r.hud_edge_frac)}</strong></div>`;
  if (o.detector==="splat") return `<h2>Splat reading</h2><div class="kv"><span>detected</span><strong>${r.detected}</strong><span>skull</span><strong>${num(r.skull_score)}</strong></div>`;
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

function renderEpisodes(){
  $("episodes").innerHTML=(DATA.episodes||[]).map(ep=>{
    const latch=(ep.latch_points||[]).map(p=>`<div class="latch-item"><div class="v ${p.value?"t":"f"}">${p.value?"TRUE":"FALSE"}</div><div style="color:var(--muted);font-size:10px">${esc(p.label)}<br>${p.timestamp.toFixed(1)}s</div></div>`).join('<div style="color:var(--muted)">——</div>');
    const c=ep.completeness||{};
    const comp = `<div class="comp">
      <span class="c ${c.death?"ok":"bad"}">${c.death?"✓":"✗"} DEATH</span>
      <span class="c ${c.countdown?"ok":"bad"}">${c.countdown?"✓":"✗"} COUNTDOWN</span>
      <span class="c ${c.respawn?"ok":"bad"}">${c.respawn?"✓":"✗"} RESPAWN</span>
      <span class="c ${c.active?"ok":"bad"}">${c.active?"✓":"✗"} ACTIVE</span>
      <span style="color:var(--muted)">completeness ${ep.completeness_score||0}/${ep.completeness_total||4}</span>
    </div>`;
    const steps=(ep.steps||[]).map(st=>`<div class="step" data-oid="${esc(st.observation_id||"")}" data-tid="${esc(st.transition_id||"")}"><span>${st.timestamp.toFixed(2)}s</span><strong>${esc(st.label)}</strong><span style="color:var(--muted)">${esc(st.detail||"")}</span></div>`).join("");
    const incomplete = (ep.completeness_score||0) < (ep.completeness_total||4);
    return `<div class="episode ${incomplete?"incomplete":""}"><h3>DEATH EPISODE #${ep.index} <span style="color:var(--muted);font-weight:400">${ep.start.toFixed(2)}s</span></h3>
      ${comp}
      <div class="latch"><div style="color:var(--muted);margin-bottom:6px">countdown_confirmed_this_death_episode</div><div class="latch-row">${latch||"—"}</div></div>
      ${steps}${ep.warning?`<div style="color:var(--warn);margin-top:6px">⚠ ${esc(ep.warning)}</div>`:""}</div>`;
  }).join("") || "<div class='ph'>No episodes</div>";
  $("episodes").querySelectorAll(".step").forEach(el=>el.onclick=()=>{
    const oid=el.getAttribute("data-oid"), tid=el.getAttribute("data-tid");
    if (tid) state.selectedTransitionId=tid;
    if (oid) state.selectedId=oid;
    renderGallery(); renderDetail(); renderLifecycle(); renderDiagTimeline();
  });
}

function renderGtRadios(){
  const labels = DATA.ground_truth_labels || Object.keys(GT_LABELS);
  $("gt-radios").innerHTML = labels.map(l=>`<label><input type="radio" name="gt-label" value="${esc(l)}"/> ${esc(GT_LABELS[l]||l)}</label>`).join("");
  $("gt-radios").querySelectorAll("input").forEach(inp=>{
    inp.addEventListener("change", ()=>{
      const o=selectedObs();
      if(!o){
        alert("Select an evidence card first, then choose a ground-truth label.");
        return;
      }
      // Replace any prior mark that covers this frame.
      state.gt = state.gt.filter(g => !(o.timestamp >= g.t0 && o.timestamp <= g.t1));
      if (inp.value !== "unknown") {
        state.gt.push({t0:o.timestamp, t1:o.timestamp+0.5, label:inp.value});
      }
      saveGt();
    });
  });
}

function syncGtRadio(g){
  const label = g ? g.label : "unknown";
  $("gt-radios").querySelectorAll("input").forEach(inp=>{ inp.checked = inp.value===label; });
}

function addGtInterval(){
  const t0=parseFloat($("gt-t0").value), t1=parseFloat($("gt-t1").value);
  const selected = $("gt-radios").querySelector("input:checked");
  const label = selected ? selected.value : "unknown";
  if (Number.isNaN(t0)||Number.isNaN(t1)||t1<=t0){ alert("Invalid interval — end must be after start"); return; }
  if (!selected || label==="unknown"){
    alert("Pick a label (e.g. Real death or Damage/recovery), then click Mark interval.");
    return;
  }
  // Drop overlapping marks with the same label window.
  state.gt = state.gt.filter(g => g.t1 <= t0 || g.t0 >= t1);
  state.gt.push({t0,t1,label});
  state.gt.sort((a,b)=>a.t0-b.t0);
  saveGt();
}

function exportGt(){
  const payload = {
    manifest_path: DATA.manifest_path,
    video_label: DATA.summary.video_label,
    exported_at: new Date().toISOString(),
    intervals: state.gt,
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
    <div class="gt-item"><span>${g.t0.toFixed(2)}–${g.t1.toFixed(2)}s · <strong>${esc(GT_LABELS[g.label]||g.label)}</strong></span>
    <button type="button" data-i="${i}">remove</button></div>`).join("") : "<div style='color:var(--muted)'>No marks yet — pick a radio on a selected frame, or Mark interval</div>";
  $("gt-list").querySelectorAll("button[data-i]").forEach(btn=>btn.onclick=()=>{
    state.gt.splice(parseInt(btn.getAttribute("data-i"),10),1); saveGt();
  });
}

function renderStats(){
  const deathEvents = (DATA.markers||[]).filter(m=>m.label==="DEATH" && String(m.id).startsWith("event:"));
  const real = state.gt.filter(g=>g.label==="real_death");
  const negLabels = new Set(["not_a_death","damage_recovery","results_screen","lobby"]);
  let tp=0, fn=0;
  for (const g of real){
    const hit = deathEvents.some(e => e.timestamp >= g.t0-1 && e.timestamp <= g.t1+1);
    if (hit) tp++; else fn++;
  }
  let fp=0;
  for (const e of deathEvents){
    const g = gtAt(e.timestamp);
    if (g && negLabels.has(g.label)) fp++;
  }
  // Also count confirmed diagnostics overlapping neg GT
  const precision = (tp+fp)>0 ? tp/(tp+fp) : null;
  const recall = (tp+fn)>0 ? tp/(tp+fn) : null;
  $("stats-grid").innerHTML = `
    <div><strong>${tp}</strong><span>True positives</span></div>
    <div><strong>${fp}</strong><span>False positives</span></div>
    <div><strong>${fn}</strong><span>Missed deaths</span></div>
    <div><strong>${precision==null?"—":(precision*100).toFixed(0)+"%" }</strong><span>Precision</span></div>
    <div><strong>${recall==null?"—":(recall*100).toFixed(0)+"%" }</strong><span>Recall</span></div>
    <div><strong>${state.gt.length}</strong><span>GT intervals</span></div>
    <div><strong>${DATA.summary.detector_positive_event_negative||0}</strong><span>Det+/evt−</span></div>
    <div><strong>${DATA.summary.death_suppressed||0}</strong><span>Suppressed</span></div>`;
}

init();
window.addEventListener("resize",()=>{ const o=selectedObs(); if(o) updateRoi(o); });
</script>
</body>
</html>
"""
