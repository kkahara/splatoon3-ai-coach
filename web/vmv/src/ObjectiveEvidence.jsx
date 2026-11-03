import { Disclosure } from "./Disclosure.jsx";
import { formatTime } from "./labels.js";
import { humanize, MODE_LABELS, objectiveSections, progressRows, supportingEvidence } from "./objectiveEvidence.js";

function Facts({ rows }) {
  return <dl className="fact-list">{rows.map(([label, value]) => (
    <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
  ))}</dl>;
}

function Support({ references, onViewEvidence }) {
  if (!references.length) return <p className="muted">Supporting provenance unavailable.</p>;
  return <Disclosure title="Supporting evidence">
    <ul className="objective-support">{references.map((ref, i) => <li key={`${ref.evidenceId || ref.frameId}-${i}`}>
      <p>{humanize(ref.role)} · Video {formatTime(ref.videoTime)}
        {ref.cue ? ` · ${humanize(ref.cue)}` : ""}
        {ref.language ? ` · ${ref.language.toUpperCase()}` : ""}
        {ref.template ? ` · Template ${ref.template}` : ""}</p>
      {ref.evidenceId && <p>Detector evidence <code>{ref.evidenceId}</code></p>}
      {ref.frameId && <p>Frame <code>{ref.frameId}</code></p>}
      {ref.sourceFrame && <Facts rows={[
        ["Frame index", ref.sourceFrame.source_frame_index ?? "Unavailable"],
        ["Source PTS", ref.sourceFrame.source_pts ?? "Unavailable"],
        ["Time base", ref.sourceFrame.source_time_base_num != null && ref.sourceFrame.source_time_base_den != null
          ? `${ref.sourceFrame.source_time_base_num}/${ref.sourceFrame.source_time_base_den}` : "Unavailable"],
      ]} />}
      {onViewEvidence && <button type="button" onClick={() => onViewEvidence(ref)}>
        {ref.role === "sample" ? "View sample frame" : "View supporting frame"}
      </button>}
    </li>)}</ul>
  </Disclosure>;
}

export function ObjectiveEvidence({ objective, onViewEvidence }) {
  if (!objective) return <section className="objective-evidence">
    <h3>Objective evidence</h3><p className="muted">Objective context unavailable in this artifact.</p>
  </section>;
  const sections = objectiveSections(objective);
  const progress = progressRows(objective.progress);
  const transitions = objective.mode === "splat_zones" ? objective.control?.transitions || [] : [];
  return <section className="objective-evidence">
    <h3>Objective evidence · {MODE_LABELS[objective.mode] || objective.mode}</h3>
    <p className="muted">Persisted scenario evidence · Times are video observation times.</p>
    {objective.limitations?.length > 0 && <p>{objective.limitations.join(" · ")}</p>}
    {objective.validation_status && <p>Validation: {humanize(objective.validation_status)}</p>}
    {!sections.length && <p className="muted">Unsupported objective payload.</p>}
    {sections.map((section) => <div key={section.title} className="objective-stream">
      <h4>{section.title}</h4>
      <div className="objective-samples">{section.samples.map((s) => <article className="objective-sample" key={s.key}>
        <h5>{s.label}</h5>
        {s.available ? <>
          <p>Sample video time <strong>{formatTime(s.time)}</strong>
            {s.quality && <> · <span className="objective-quality">{humanize(s.quality)}</span></>}</p>
          {s.establishedTime != null && <p>State established at video <strong>{formatTime(s.establishedTime)}</strong></p>}
          <Facts rows={s.fields} />
          <Support references={s.supports} onViewEvidence={onViewEvidence} />
        </> : <p className="muted">No sample / unavailable.</p>}
      </article>)}</div>
    </div>)}
    {transitions.length > 0 && <Disclosure title={`Confirmed zone transitions (${transitions.length})`}>
      {transitions.map((t, i) => <div key={i}>
        <p>{formatTime(t.video_time)} · {humanize(t.from_state)} → {humanize(t.to_state)}</p>
        <Support references={supportingEvidence(t)} onViewEvidence={onViewEvidence} />
      </div>)}
    </Disclosure>}
    <h4>Objective progress</h4>
    {progress.length ? <Disclosure title={`Progress observations (${progress.length})`}>
      <div className="objective-table"><table><thead><tr>
        <th>Video time</th><th>Team</th><th>Native value</th><th>Progress</th><th>Count units</th><th>Evidence</th>
      </tr></thead><tbody>{progress.map((row, i) => <tr key={i}>
        <td>{formatTime(row.time)}</td><td>{row.team}</td><td title={row.label}>{row.value}</td>
        <td>{row.state}</td><td>{row.amount}</td>
        <td><Support references={row.supports} onViewEvidence={onViewEvidence} /></td>
      </tr>)}</tbody></table></div>
      <p className="muted">Sparse endpoint comparisons. Count units retain their mode-specific meaning.</p>
    </Disclosure> : <p className="muted">Unavailable.</p>}
  </section>;
}
