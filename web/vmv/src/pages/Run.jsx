import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api.js";
import {
  CHIP_DETECTORS,
  chipText,
  groupTiles,
  nearestTile,
  placeMarks,
  preferredReading,
  tilePasses,
} from "../cadence.js";
import { GT_LABELS, GT_MEANINGS, detectorForChannel, formatTime, labelsFor } from "../labels.js";
import { Disclosure } from "../Disclosure.jsx";
import { ScenarioStudio } from "./ScenarioStudio.jsx";

function mediaUrl(runId, relpath) {
  const parts = relpath.split("/").map((part) => encodeURIComponent(part));
  return `/api/runs/${encodeURIComponent(runId)}/media/${parts.join("/")}`;
}

export function RunPage() {
  const { id } = useParams();
  const runId = decodeURIComponent(id);
  const [payload, setPayload] = useState(null);
  const [truth, setTruth] = useState(null);
  const [error, setError] = useState("");
  const [detector, setDetector] = useState("");
  const [channel, setChannel] = useState("evidence");
  const [t0, setT0] = useState("0");
  const [t1, setT1] = useState("");
  const [confidence, setConfidence] = useState(0);
  const [selectedId, setSelectedId] = useState(null);
  const [population, setPopulation] = useState("all");
  const [candidateIndex, setCandidateIndex] = useState(0);
  const [scenarioIndex, setScenarioIndex] = useState(0);
  const [playhead, setPlayhead] = useState(null);
  const videoRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api(`/api/runs/${encodeURIComponent(runId)}`),
      api(`/api/runs/${encodeURIComponent(runId)}/ground-truth`),
    ]).then(([view, gt]) => {
      if (cancelled) return;
      setPayload(view);
      setTruth(gt);
      const queue = view.view.review_queue || [];
      if (queue.length > 0) setChannel("special_usage");
    }).catch((exc) => setError(exc.message));
    return () => {
      cancelled = true;
    };
  }, [runId]);

  const view = payload?.view;
  const observations = view?.observations || [];
  const allTiles = useMemo(() => groupTiles(observations), [observations]);
  const tiles = useMemo(() => {
    const start = Number(t0 || 0);
    const end = t1 === "" ? Number.POSITIVE_INFINITY : Number(t1);
    return allTiles.filter((tile) => {
      if (tile.timestamp < start || tile.timestamp > end) return false;
      return tilePasses(tile, detector, confidence);
    });
  }, [allTiles, t0, t1, detector, confidence]);

  const selected = observations.find((obs) => obs.id === selectedId)
    || preferredReading(tiles[0], detector)
    || null;
  const duration = view?.summary?.duration_seconds || 1;
  const scenarios = view?.scenario_evidence || [];
  const scenario = scenarios[scenarioIndex] || null;

  useEffect(() => {
    const node = videoRef.current;
    if (!scenario || !node) return undefined;
    const time = Math.max(0, Number(scenario.start_time) || 0);
    const apply = () => {
      node.pause();
      node.currentTime = time;
    };
    if (node.readyState >= 1) apply();
    else node.addEventListener("loadedmetadata", apply, { once: true });
    return undefined;
  }, [scenarioIndex, scenario?.scenario_id, scenario?.start_time]);
  const queue = (view?.review_queue || []).filter((item) => population === "all" || item.population === population);
  const candidate = queue[candidateIndex] || null;
  const markDetector = detectorForChannel(channel, selected?.detector);
  const choices = labelsFor(channel, selected?.detector);
  const currentMark = truth?.intervals?.find(
    (item) => selected && item.t0 === selected.timestamp && item.detector === markDetector,
  );

  async function saveIntervals(intervals) {
    const saved = await api(`/api/runs/${encodeURIComponent(runId)}/ground-truth`, {
      method: "PUT",
      body: JSON.stringify({ ...truth, intervals, video_label: runId }),
    });
    setTruth(saved);
  }

  async function mark(label) {
    if (!selected || !truth) return;
    const rest = truth.intervals.filter(
      (item) => !(item.t0 === selected.timestamp && item.detector === markDetector),
    );
    await saveIntervals([
      ...rest,
      { t0: selected.timestamp, t1: selected.timestamp, detector: markDetector, label },
    ]);
  }

  function onDetector(value) {
    setDetector(value);
    if (value === "special_gauge") setChannel("special_usage");
  }

  function seekVideo(time) {
    const node = videoRef.current;
    if (!node || time == null || Number.isNaN(time)) return;
    const apply = () => {
      node.currentTime = time;
    };
    if (node.readyState >= 1) apply();
    else node.addEventListener("loadedmetadata", apply, { once: true });
  }

  function revealTile(tile) {
    const start = Number(t0 || 0);
    const end = t1 === "" ? Number.POSITIVE_INFINITY : Number(t1);
    if (tile.timestamp < start) setT0(Math.max(0, tile.timestamp - 0.5).toFixed(1));
    if (Number.isFinite(end) && tile.timestamp > end) setT1((tile.timestamp + 0.5).toFixed(1));
  }

  function focusTime(time) {
    const tile = nearestTile(allTiles, time);
    if (tile) {
      revealTile(tile);
      const reading = preferredReading(tile, detector);
      if (reading) setSelectedId(reading.id);
    }
    setPlayhead(time);
    seekVideo(time);
  }

  function selectReading(reading) {
    if (!reading) return;
    setSelectedId(reading.id);
    setPlayhead(reading.timestamp);
    seekVideo(reading.timestamp);
  }

  useEffect(() => {
    if (!selectedId) return;
    const tile = allTiles.find((item) => item.readings.some((reading) => reading.id === selectedId));
    if (!tile) return;
    const node = document.querySelector(`[data-tile="${CSS.escape(tile.key)}"]`);
    const gallery = node?.closest(".gallery");
    if (!node || !gallery) return;
    const delta = node.getBoundingClientRect().left - gallery.getBoundingClientRect().left;
    const target = gallery.scrollLeft + delta - (gallery.clientWidth - node.offsetWidth) / 2;
    gallery.scrollLeft = Math.max(0, target);
  }, [selectedId, allTiles, tiles]);

  function timeFromPointer(event) {
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = rect.width ? (event.clientX - rect.left) / rect.width : 0;
    return Math.min(duration, Math.max(0, ratio * duration));
  }

  if (error) return <p className="error">{error}</p>;
  if (!view) return <p>Loading run…</p>;

  const summary = view.summary;
  const identity = view.match_identity;
  const colors = view.team_colors;
  const stripWidth = Math.max(960, Math.ceil(duration * 8));
  const timeline = placeMarks(view.lifecycle_marks || [], duration, stripWidth);
  const tickStep = duration > 180 ? 30 : duration > 60 ? 20 : 10;
  const ticks = [];
  for (let tick = 0; tick <= duration + 0.01; tick += tickStep) ticks.push(tick);
  const activeMark = playhead == null
    ? null
    : timeline.placed.reduce((best, item) => {
      const distance = Math.abs(item.mark.timestamp - playhead);
      if (distance > 0.4) return best;
      if (!best || distance < Math.abs(best.mark.timestamp - playhead)) return item;
      return best;
    }, null);

  return (
    <div className="stack">
      <section className="header-card">
        <h1>{summary.video_label}</h1>
        <div className="meta-chips">
          <span>Language {summary.language ? summary.language.toUpperCase() : "—"}</span>
          <span>Stage {identity?.stage_id || "—"}</span>
          <span>Mode {identity?.battle_mode_id || "—"}</span>
          <span>{formatTime(summary.duration_seconds)}</span>
          <span>{summary.frame_count} frames</span>
          {colors?.ally && <span className="swatch" style={{ background: colors.ally.css_hex }}>Ally</span>}
          {colors?.opponent && <span className="swatch" style={{ background: colors.opponent.css_hex }}>Opponent</span>}
        </div>
        {(summary.warnings || []).map((warning) => (
          <p key={warning.message} className={warning.level}>{warning.message}</p>
        ))}
      </section>

      <section className="row filters">
        <label>Time start<input value={t0} onChange={(event) => setT0(event.target.value)} /></label>
        <label>Time end<input value={t1} onChange={(event) => setT1(event.target.value)} placeholder="end" /></label>
        <label>Detector
          <select value={detector} onChange={(event) => onDetector(event.target.value)}>
            <option value="">All</option>
            {(view.detectors || []).map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </label>
        <label>GT channel
          <select value={channel} onChange={(event) => setChannel(event.target.value)}>
            <option value="evidence">Evidence</option>
            <option value="special_usage">Special usage</option>
            <option value="match_phase">Match phase</option>
          </select>
        </label>
        <label>Confidence ≥
          <input type="number" min="0" max="1" step="0.05" value={confidence} onChange={(event) => setConfidence(Number(event.target.value))} />
        </label>
      </section>

      <section>
        <h2>Lifecycle</h2>
        <div className="life-scroll">
          <div className="life-canvas" style={{ width: stripWidth }}>
            <div className="life-hit" onClick={(event) => focusTime(timeFromPointer(event))}>
            <div className="axis">
              {ticks.map((tick) => (
                <span key={tick} className="tick" style={{ left: `${(tick / duration) * 100}%` }}>
                  {formatTime(tick)}
                </span>
              ))}
            </div>
            <div className="life-track">
              {(view.lifecycle_segments || []).map((segment) => (
                <div
                  key={`${segment.phase}-${segment.start}`}
                  className={`life-seg phase-${segment.phase}`}
                  style={{
                    left: `${(segment.start / duration) * 100}%`,
                    width: `${(Math.max(segment.end - segment.start, 0) / duration) * 100}%`,
                  }}
                  title={`${segment.phase} ${formatTime(segment.start)}`}
                />
              ))}
              {playhead != null && (
                <div className="playhead" style={{ left: `${(playhead / duration) * 100}%` }} />
              )}
            </div>
            <div className="life-labels" style={{ height: timeline.rowHeight }}>
              {timeline.placed.map(({ mark, leftPct, lane }) => {
                const phase = String(mark.phase_label || "").toLowerCase().replaceAll("_", "-");
                const picked = activeMark?.mark === mark;
                return (
                  <button
                    type="button"
                    key={`${mark.timestamp}-${mark.event_label}-${lane}`}
                    className={`life-mark phase-${phase} ${picked ? "selected" : ""}`}
                    style={{ left: `${leftPct}%`, top: lane * 46 }}
                    onClick={(event) => {
                      event.stopPropagation();
                      focusTime(mark.timestamp);
                    }}
                  >
                    <span className="dot" />
                    <span className="stem" />
                    <span className="lbl">
                      <span className="t">{formatTime(mark.timestamp)}</span>
                      <span className="e">{mark.event_label}</span>
                    </span>
                  </button>
                );
              })}
            </div>
            </div>
          </div>
        </div>
      </section>

      <section>
        <h2>Cadence frames</h2>
        <div className="gallery">
          {tiles.map((tile) => {
            const reading = preferredReading(tile, detector);
            const picked = tile.readings.some((item) => item.id === selectedId);
            return (
              <div
                key={tile.key}
                data-tile={tile.key}
                className={picked ? "cadence-card selected" : "cadence-card"}
                onClick={() => selectReading(reading)}
              >
                {tile.image_relpath ? (
                  <img src={mediaUrl(runId, tile.image_relpath)} alt="" />
                ) : <span className="ph">No image</span>}
                <span className="when">{formatTime(tile.timestamp)}</span>
                <div className="badge-grid">
                  {CHIP_DETECTORS.map((name) => {
                    const chip = tile.readings.find((item) => item.detector === name);
                    const on = chip?.positive ? "on" : "";
                    const active = chip && chip.id === selectedId ? "picked" : "";
                    if (!chip) {
                      return <span key={name} className="badge">{name.toUpperCase()} −</span>;
                    }
                    return (
                      <button
                        type="button"
                        key={name}
                        className={`badge ${on} ${active} ${name === "splat" ? "splat" : ""}`}
                        onClick={(event) => {
                          event.stopPropagation();
                          selectReading(chip);
                        }}
                      >
                        {chipText(chip)}
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="split">
        <div>
          <h2>Selected observation</h2>
          {selected ? (
            <>
              <p>{formatTime(selected.timestamp)} · {selected.detector || "frame"} · {selected.lifecycle || "—"}</p>
              {selected.image_relpath && (
                <img
                  className="preview"
                  alt="selected frame"
                  src={mediaUrl(runId, selected.image_relpath)}
                />
              )}
              <dl className="kv">
                <dt>Confidence</dt><dd>{selected.confidence ?? "—"}</dd>
                <dt>Match phase</dt><dd>{selected.match_phase || "—"}</dd>
                <dt>Positive</dt><dd>{String(selected.positive)}</dd>
              </dl>
              <Disclosure key={selected.id || selected.timestamp} title="Raw JSON">
                <pre>{JSON.stringify(selected.reading, null, 2)}</pre>
              </Disclosure>
            </>
          ) : <p>No observation in this filter.</p>}
        </div>
        <div>
          <h2>Ground truth</h2>
          <p className="muted">Marks are review data. Saving them does not change the manifest or retrain a detector.</p>
          <p>Channel detector: {markDetector}</p>
          {GT_MEANINGS[choices[1]] && <p className="muted">{GT_MEANINGS[choices[1]]}</p>}
          <div className="row">
            {choices.map((label) => (
              <label key={label}>
                <input
                  type="radio"
                  name="gt"
                  checked={currentMark?.label === label}
                  onChange={() => mark(label).catch((exc) => setError(exc.message))}
                />
                {GT_LABELS[label] || label}
              </label>
            ))}
          </div>
          <button type="button" onClick={() => saveIntervals([]).catch((exc) => setError(exc.message))}>Clear all GT</button>
          <ul>
            {(truth?.intervals || []).map((item) => (
              <li key={`${item.detector}-${item.t0}-${item.label}`}>
                {formatTime(item.t0)} {item.detector} {GT_LABELS[item.label] || item.label}
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section>
        <h2>Special-used review</h2>
        <div className="row">
          {["all", "extra", "post_death", "other"].map((name) => (
            <button type="button" key={name} onClick={() => { setPopulation(name); setCandidateIndex(0); }}>{name}</button>
          ))}
          <button type="button" onClick={() => setCandidateIndex((index) => Math.max(0, index - 1))}>Prev</button>
          <button type="button" onClick={() => setCandidateIndex((index) => Math.min(queue.length - 1, index + 1))}>Next</button>
          <span>{queue.length === 0 ? "0" : `${candidateIndex + 1} / ${queue.length}`}</span>
        </div>
        {candidate && (
          <dl className="kv">
            <dt>Population</dt><dd>{candidate.population}</dd>
            <dt>Peak</dt><dd>{formatTime(candidate.peak_time)} fill {candidate.peak_fill}</dd>
            <dt>Trough</dt><dd>{formatTime(candidate.trough_time)} fill {candidate.trough_fill}</dd>
            <dt>Decline</dt><dd>{candidate.decline}</dd>
            <dt>Nearest death</dt><dd>{candidate.nearest_death_signed_seconds ?? "—"}</dd>
          </dl>
        )}
      </section>

      <ScenarioStudio
        runId={runId}
        scenarios={scenarios}
        scenarioIndex={scenarioIndex}
        onSelect={(index) => setScenarioIndex(Math.max(0, Math.min(scenarios.length - 1, index)))}
        videoRef={videoRef}
        videoUrl={view.review_video_url}
        onRefresh={async () => {
          const next = await api(`/api/runs/${encodeURIComponent(runId)}`);
          setPayload(next);
        }}
      />
    </div>
  );
}
