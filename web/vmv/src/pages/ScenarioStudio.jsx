import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Disclosure } from "../Disclosure.jsx";
import { formatTime } from "../labels.js";

const REVIEW_STATUSES = [
  ["needs_review", "Needs review"],
  ["evidence_verified", "Evidence verified"],
  ["evidence_problem", "Evidence problem"],
  ["coaching_problem", "Coaching problem"],
];

const SPECIAL_GAP =
  "Special-gauge review marks are not SPECIAL_USED coaching evidence, because fusion does not emit that event.";

export function ScenarioStudio({ runId, scenarios, scenarioIndex, onSelect, videoRef, videoUrl, onRefresh }) {
  const scenario = scenarios[scenarioIndex] || null;
  const [layers, setLayers] = useState(null);
  const [experiments, setExperiments] = useState([]);
  const [review, setReview] = useState(null);
  const [jobNote, setJobNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [activeAction, setActiveAction] = useState(null);
  const [localError, setLocalError] = useState("");

  useEffect(() => {
    if (!scenario) return undefined;
    let cancelled = false;
    const scenarioId = scenario.scenario_id;
    setLayers(null);
    setExperiments([]);
    Promise.all([
      api(`/api/runs/${encodeURIComponent(runId)}/coach-layers?scenario_id=${encodeURIComponent(scenarioId)}`),
      api(`/api/runs/${encodeURIComponent(runId)}/coach-experiments?scenario_id=${encodeURIComponent(scenarioId)}`),
      api(`/api/runs/${encodeURIComponent(runId)}/scenario-review`),
    ]).then(([nextLayers, nextExperiments, nextReview]) => {
      if (cancelled) return;
      setLayers(nextLayers);
      setExperiments(nextExperiments);
      setReview(nextReview);
    }).catch((exc) => {
      if (!cancelled) setLocalError(exc.message);
    });
    return () => {
      cancelled = true;
    };
  }, [runId, scenario]);

  if (!scenario) return <p className="muted">This run has no scenarios.</p>;

  const importance = layers?.llm_view?.importance || null;
  const selected = importance?.selected_for_llm === true;
  const deathEpisode = scenario.scenario_type === "death_episode";
  const unit = layers?.coaching_unit || scenario.coaching || null;
  const factors = (unit?.factors || []).filter((factor) => factor.active);
  const mark = review?.marks?.find((item) => item.scenario_id === scenario.scenario_id);
  const status = mark?.status || "needs_review";
  const commands = [
    `s3-coach coach-inputs analysis/${runId}`,
    `s3-coach coach-prototype analysis/${runId}`,
    `s3-coach coach-prototype analysis/${runId} --call-llm`,
  ];

  async function runCoaching() {
    beginJob("prepare");
    try {
      const created = await api(`/api/runs/${encodeURIComponent(runId)}/coach`, { method: "POST", body: "{}" });
      const finished = await pollJob(created.job_id, setJobNote);
      if (finished.status !== "completed") {
        throw new Error(finished.error?.message || finished.status);
      }
      setJobNote("finishing");
      await reloadScenario();
      if (onRefresh) await onRefresh();
    } catch (exc) {
      setLocalError(exc.message);
    } finally {
      endJob();
    }
  }

  async function runPrototypeLlm() {
    beginJob("prototype");
    try {
      const created = await api(`/api/runs/${encodeURIComponent(runId)}/coach-llm`, { method: "POST", body: "{}" });
      const finished = await pollJob(created.job_id, setJobNote);
      if (finished.status !== "completed") {
        throw new Error(finished.error?.message || finished.status);
      }
      setJobNote("finishing");
      await reloadScenario();
      if (onRefresh) await onRefresh();
    } catch (exc) {
      setLocalError(exc.message);
    } finally {
      endJob();
    }
  }

  function beginJob(action) {
    setBusy(true);
    setActiveAction(action);
    setJobNote("queued");
    setLocalError("");
  }

  function endJob() {
    setBusy(false);
    setActiveAction(null);
    setJobNote("");
  }

  async function reloadScenario() {
    const scenarioId = scenario.scenario_id;
    const [nextLayers, nextExperiments, nextReview] = await Promise.all([
      api(`/api/runs/${encodeURIComponent(runId)}/coach-layers?scenario_id=${encodeURIComponent(scenarioId)}`),
      api(`/api/runs/${encodeURIComponent(runId)}/coach-experiments?scenario_id=${encodeURIComponent(scenarioId)}`),
      api(`/api/runs/${encodeURIComponent(runId)}/scenario-review`),
    ]);
    setLayers(nextLayers);
    setExperiments(nextExperiments);
    setReview(nextReview);
  }

  async function runExperiment() {
    beginJob("experiment");
    try {
      const created = await api(`/api/runs/${encodeURIComponent(runId)}/coach-experiment`, {
        method: "POST",
        body: JSON.stringify({ scenario_id: scenario.scenario_id }),
      });
      const finished = await pollJob(created.job_id, setJobNote);
      if (finished.status !== "completed") {
        throw new Error(finished.error?.message || finished.status);
      }
      setJobNote("finishing");
      const next = await api(
        `/api/runs/${encodeURIComponent(runId)}/coach-experiments?scenario_id=${encodeURIComponent(scenario.scenario_id)}`,
      );
      setExperiments(next);
    } catch (exc) {
      setLocalError(exc.message);
    } finally {
      endJob();
    }
  }

  async function saveStatus(nextStatus) {
    const rest = (review?.marks || []).filter((item) => item.scenario_id !== scenario.scenario_id);
    const saved = await api(`/api/runs/${encodeURIComponent(runId)}/scenario-review`, {
      method: "PUT",
      body: JSON.stringify({ marks: [...rest, { scenario_id: scenario.scenario_id, status: nextStatus }] }),
    });
    setReview(saved);
  }

  const findings = factors.map((factor) => factor.statement_player).filter(Boolean);
  const facts = evidenceFacts(scenario);
  const newest = experiments[0] || null;
  const older = experiments.slice(1);

  return (
    <section className="scenario-focus card">
      <div className="row">
        <button type="button" disabled={scenarioIndex === 0} onClick={() => onSelect(scenarioIndex - 1)}>← Previous</button>
        <button type="button" disabled={scenarioIndex >= scenarios.length - 1} onClick={() => onSelect(scenarioIndex + 1)}>Next →</button>
        <span className="muted">{scenarioIndex + 1} / {scenarios.length}</span>
      </div>
      {videoUrl ? (
        <video ref={videoRef} key={videoUrl} controls playsInline preload="metadata" src={videoUrl} />
      ) : <p className="muted">No source video is available for this run.</p>}
      {localError && <p className="error">{localError}</p>}
      {activeAction && <JobStatus action={activeAction} phase={jobNote} />}

      <header className="scenario-summary">
        <h2>{titleCaseType(scenario.scenario_type)} · {formatTime(scenario.start_time)}</h2>
        {importance ? (
          <p>
            Importance {importance.importance_score} · Rank #{importance.rank} ·{" "}
            {selected ? "Selected for LLM" : "Not selected for LLM"}
          </p>
        ) : null}
      </header>
      {findings.length > 0 && (
        <aside className="key-finding">
          <h3>Key finding</h3>
          {findings.map((line) => <p key={line}>{line}</p>)}
        </aside>
      )}

      <section className="funnel">
        <h2>Evidence</h2>
        {facts.length > 0 ? (
          <dl className="fact-list">
            {facts.map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        ) : null}
        <Disclosure title="Timeline"><JsonBody value={scenario.timeline} /></Disclosure>
        <Disclosure title="Combat"><JsonBody value={scenario.combat} /></Disclosure>
        <Disclosure title="Map / ink"><JsonBody value={scenario.map} /></Disclosure>
        <Disclosure title="Players"><JsonBody value={scenario.players} /></Disclosure>
        <p>{SPECIAL_GAP}</p>
        <Disclosure title="Special gauge"><JsonBody value={scenario.special} /></Disclosure>
        <Disclosure title="Relations"><JsonBody value={scenario.relations} /></Disclosure>
        <Disclosure title="Raw event timeline">
          <pre>{(scenario.event_ids || []).join(", ") || "—"}</pre>
        </Disclosure>
        <Disclosure key={`${scenario.scenario_id}-raw`} title="Developer: raw JSON">
          <pre>{JSON.stringify(scenario, null, 2)}</pre>
        </Disclosure>
      </section>

      <section className="funnel">
        <h2>Why this scenario was selected</h2>
        {importance ? (
          <>
            <p>
              {importance.importance_score} · Rank #{importance.rank} ·{" "}
              {selected ? "Selected for LLM" : "Not selected for LLM"}
            </p>
            <ul className="reason-list">
              {factors.map((factor) => (
                <li key={factor.factor_id}>
                  <span>+{factor.contribution}</span>
                  {factor.statement_player || factor.factor_id}
                </li>
              ))}
              {factors.length === 0 && <li className="muted">None</li>}
            </ul>
            <p>
              {selected
                ? "Selected by the official top-N gate."
                : "Not selected by the official top-N gate. You can send it manually to inspect what the model would produce."}
            </p>
          </>
        ) : <p className="muted">Coaching inputs have not been generated.</p>}
      </section>

      <section className="funnel">
        <h2>Official coaching</h2>
        <h3>Preparation</h3>
        <p className="muted">Prepare coaching ranks death episodes and writes the prompts. It does not call the model.</p>
        {!deathEpisode && (
          <p>This scenario is not a primary coaching unit. coach-inputs builds primary units for death episodes only.</p>
        )}
        {layers ? (
          <ul className="prep-list">
            <li>{layers.llm_view ? "CoachLlmView prepared" : "CoachLlmView not prepared"}</li>
            <li>{selected ? "Selected for LLM" : "Not selected for LLM"}</li>
          </ul>
        ) : <p className="muted">Loading coaching layers…</p>}
        <div className="row">
          <button type="button" disabled={busy} aria-busy={activeAction === "prepare"} onClick={() => runCoaching()}>
            {activeAction === "prepare" ? "Preparing coaching…" : "Prepare coaching"}
          </button>
          <button type="button" disabled={busy} aria-busy={activeAction === "prototype"} onClick={() => runPrototypeLlm()}>
            {activeAction === "prototype" ? "Running prototype LLM…" : "Run prototype LLM"}
          </button>
        </div>
        {activeAction === "prepare" && <JobStatus action="prepare" phase={jobNote} inline />}
        {activeAction === "prototype" && <JobStatus action="prototype" phase={jobNote} inline />}

        <h3>Deterministic coaching signal</h3>
        {factors.length === 0 && <p className="muted">No active factor annotations.</p>}
        {factors.map((factor) => (
          <article key={factor.factor_id}>
            <p>{factor.statement_player || factor.factor_id}</p>
            {factor.interpretation ? <p><strong>Interpretation</strong><br />{factor.interpretation}</p> : null}
            {factor.recommendation ? <p><strong>Recommendation</strong><br />{factor.recommendation}</p> : null}
          </article>
        ))}

        <h3>Prototype assessment</h3>
        <p className="muted">Calls the provider for units marked selected_for_llm. Requires coach-inputs first.</p>
        {(layers?.prototype_assessments || []).length === 0 ? (
          <>
            <p>Coaching has not been generated.</p>
            <pre>{commands.join("\n")}</pre>
          </>
        ) : layers.prototype_assessments.map((item) => (
          <article key={item.model}>
            <p className="muted">{item.model}</p>
            <AssessmentProse assessment={item.assessment} />
            <AssessmentAudit assessment={item.assessment} />
          </article>
        ))}
        {layers?.llm_view ? (
          <Disclosure key={`${scenario.scenario_id}-llm-view`} title="CoachLlmView">
            <pre>{JSON.stringify(layers.llm_view, null, 2)}</pre>
          </Disclosure>
        ) : <p className="muted">No saved CoachLlmView for this scenario.</p>}
        {layers?.system_prompt ? (
          <Disclosure key={`${scenario.scenario_id}-system-prompt`} title="System prompt">
            <pre>{layers.system_prompt}</pre>
          </Disclosure>
        ) : <p className="muted">Loading system prompt…</p>}
      </section>

      <section className="funnel experiment-block">
        <h2>LLM experiment</h2>
        <p>This is separate from official coaching. It does not affect ranking or official coaching.</p>
        <p className="muted">
          Provider: {layers?.llm?.provider || "—"} · Model: {layers?.llm?.model || "—"}
        </p>
        <div className="row">
          <button type="button" disabled={busy || !layers?.llm_view} aria-busy={activeAction === "experiment"} onClick={() => runExperiment()}>
            {activeAction === "experiment" ? "Running experiment…" : "Run experiment"}
          </button>
          {activeAction === "experiment" && <JobStatus action="experiment" phase={jobNote} inline />}
        </div>
        {layers && !layers.llm_view && (
          <p>Prepare coaching writes a CoachLlmView for ranked death episodes. Open one of those scenarios to run an experiment.</p>
        )}
        {newest && <ExperimentArticle item={newest} />}
        {older.length > 0 && (
          <Disclosure title="Previous experimental assessments">
            {older.map((item) => <ExperimentArticle key={item.experiment_id} item={item} />)}
          </Disclosure>
        )}
      </section>

      <section className="funnel">
        <h2>Human review</h2>
        <div className="row">
          {REVIEW_STATUSES.map(([value, label]) => (
            <label key={value}>
              <input
                type="radio"
                name={`review-${scenario.scenario_id}`}
                checked={status === value}
                onChange={() => saveStatus(value).catch((exc) => setLocalError(exc.message))}
              />
              {label}
            </label>
          ))}
        </div>
      </section>
    </section>
  );
}

function titleCaseType(type) {
  return String(type || "scenario")
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function evidenceFacts(scenario) {
  const facts = [];
  const recovery = scenario.recovery || {};
  const map = scenario.map || {};
  const combat = scenario.combat || {};
  if (scenario.scenario_type === "death_episode") {
    if (recovery.death_time != null) facts.push(["Death", formatTime(recovery.death_time)]);
    if (recovery.respawn_time != null) facts.push(["Respawn", formatTime(recovery.respawn_time)]);
    if (recovery.active_again_time != null) facts.push(["Active again", formatTime(recovery.active_again_time)]);
    if (map.map_check_before_death === true) facts.push(["Map overlay", "observed before death"]);
    if (map.map_check_before_death === false) facts.push(["Map overlay", "not observed before death"]);
  } else if (scenario.scenario_type === "engagement") {
    if (combat.splat_count != null) facts.push(["Splats", String(combat.splat_count)]);
    if (combat.first_splat_time != null) facts.push(["First splat", formatTime(combat.first_splat_time)]);
    if (combat.last_splat_time != null) facts.push(["Last splat", formatTime(combat.last_splat_time)]);
  } else if (scenario.start_time != null && scenario.end_time != null) {
    facts.push(["Interval", `${formatTime(scenario.start_time)}–${formatTime(scenario.end_time)}`]);
  }
  return facts;
}

function JsonBody({ value }) {
  const empty = value == null || value === "" || (Array.isArray(value) && value.length === 0);
  if (empty) return <p className="muted">—</p>;
  return <pre>{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre>;
}

function AssessmentProse({ assessment }) {
  if (!assessment) return <p className="muted">No parsed assessment.</p>;
  return <p className="takeaway">{assessment.assessment || "—"}</p>;
}

function AssessmentAudit({ assessment }) {
  if (!assessment) return null;
  return (
    <>
      <Disclosure title="Evidence used"><p>{joinList(assessment.evidence_used)}</p></Disclosure>
      <Disclosure title="Limitations"><p>{joinList(assessment.limitations)}</p></Disclosure>
      <Disclosure title="Recommendations"><p>{joinList(assessment.recommendations)}</p></Disclosure>
      <Disclosure title="Selected claim IDs"><p>{joinList(assessment.selected_claim_ids, ", ")}</p></Disclosure>
    </>
  );
}

function ExperimentArticle({ item }) {
  return (
    <article>
      <h3>Experimental LLM assessment</h3>
      <p className="muted">{item.created_at} · {item.provider} · {item.model}</p>
      {item.parse_error && <p className="error">{item.parse_error}</p>}
      <AssessmentProse assessment={item.result} />
      <AssessmentAudit assessment={item.result} />
      <Disclosure title="Raw response"><pre>{item.raw_response}</pre></Disclosure>
    </article>
  );
}

function JobStatus({ action, phase, inline = false }) {
  return (
    <p className={inline ? "job-status inline" : "job-status"} role={inline ? undefined : "status"}>
      {describeJob(action, phase)}
    </p>
  );
}

function describeJob(action, phase) {
  const label = {
    prepare: "Prepare coaching",
    prototype: "Run prototype LLM",
    experiment: "Run experiment",
  }[action] || "Coaching job";
  if (phase === "finishing") return `${label} finished. Updating this scenario.`;
  if (!phase || phase === "queued") return `${label} is queued.`;
  if (phase === "running: coach-inputs") return `${label} is running coach-inputs.`;
  if (phase === "running: prototype-llm") return `${label} is calling the provider.`;
  if (phase === "running: llm-experiment") return `${label} is calling the provider.`;
  if (String(phase).startsWith("running")) return `${label} is running.`;
  return `${label}: ${phase}`;
}

function joinList(values, separator = "; ") {
  if (!values || values.length === 0) return "—";
  return values.join(separator);
}

async function pollJob(jobId, setJobNote) {
  for (;;) {
    const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    setJobNote(job.phase || job.status);
    if (job.status === "completed" || job.status === "failed" || job.status === "cancelled") return job;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}
