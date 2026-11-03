import { Disclosure } from "./Disclosure.jsx";
import { formatTime } from "./labels.js";

export function EvidenceSequence({ title, summary, count, disclosureTitle, rows, value, note }) {
  return <section className="scenario-detail-card">
    <h3>{title}</h3>
    <p className="sequence-summary">{summary || "Unavailable"}</p>
    {count > 0 && <Disclosure title={`${disclosureTitle} (${count})`}>
      {note && <p className="muted">{note}</p>}
      <SemanticRows rows={rows} />
      <Disclosure title="Raw data"><pre>{JSON.stringify(value, null, 2)}</pre></Disclosure>
    </Disclosure>}
    {count === 0 && value && <Disclosure title="Show evidence">
      {note && <p className="muted">{note}</p>}
      <SemanticRows rows={rows} />
      <Disclosure title="Raw data"><pre>{JSON.stringify(value, null, 2)}</pre></Disclosure>
    </Disclosure>}
  </section>;
}

function SemanticRows({ rows }) {
  if (!rows?.length) return <p className="muted">No readable observations available.</p>;
  return <div className="evidence-rows">{rows.map((row, index) => <div className="evidence-row" key={`${row.time}-${index}`}>
    <time>{formatTime(row.time)}</time>
    <strong>{row.value}</strong>
    {row.detail && <span>{row.detail}</span>}
  </div>)}</div>;
}

export function playerEvidenceRows(scenario) {
  const players = scenario.players;
  if (!players) return [];
  const anchor = scenario.scenario_type === "death_episode" ? players.at_death : players.at_anchor;
  return (players.trajectory || []).map((sample) => {
    if (sample.reliable === false || sample.ally_alive_count == null || sample.opponent_alive_count == null) {
      return { time: sample.video_time, value: "Unavailable", detail: "Roster unreadable" };
    }
    const delta = sample.ally_alive_count - sample.opponent_alive_count;
    const advantage = delta > 0 ? `Ally +${delta}` : delta < 0 ? `Opponent +${Math.abs(delta)}` : "Even";
    const marker = sample.video_time === anchor?.video_time ? "anchor" : null;
    return {
      time: sample.video_time,
      value: `${sample.ally_alive_count}v${sample.opponent_alive_count} (${advantage})`,
      detail: marker,
    };
  });
}

export function specialEvidenceRows(special) {
  return (special?.observations || []).map((sample) => ({
    time: sample.video_time,
    value: `fill ${sample.fill_fraction == null ? "—" : sample.fill_fraction.toFixed(3)}`,
    detail: `ready ${sample.ready === true ? "yes" : sample.ready === false ? "no" : "unknown"} · visible ${sample.visible === true ? "yes" : sample.visible === false ? "no" : "unknown"}`,
  }));
}

export function playerSequenceSummary(scenario) {
  const players = scenario.players;
  if (!players) return "Roster evidence unavailable";
  const atDeath = scenario.scenario_type === "death_episode";
  const point = atDeath ? players.at_death : players.at_anchor;
  const at = point?.reliable === false || !point ? "Unavailable" :
    `${point.ally_alive_count}v${point.opponent_alive_count} ${atDeath ? "at death" : "at anchor"}`;
  const count = players.trajectory?.length || 0;
  return `${at}${count ? ` · ${count} roster samples in context` : ""}`;
}

export function specialSequenceSummary(special) {
  if (!special) return "Special evidence unavailable";
  const state = special.state_at_anchor;
  const value = state === "ready" ? "Ready" : state === "not_ready" ? "Not ready" : "Unavailable";
  const observed = special.nearest_before_anchor?.video_time;
  const count = special.observations?.length || 0;
  return `${value}${observed != null ? ` · nearest observation ${formatTime(observed)}` : ""}${count ? ` · ${count} observations in context` : ""}`;
}
