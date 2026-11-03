import { formatTime } from "./labels.js";
import { scenarioSnapshot } from "./scenarioSnapshot.js";

export function ScenarioSnapshot({ scenario }) {
  const cards = scenarioSnapshot(scenario);
  return <section className="scenario-snapshot" aria-label="Scenario snapshot">
    <h3>Scenario snapshot</h3>
    <div className="scenario-snapshot-grid">{cards.map((card) => <SnapshotCard card={card} key={card.title} />)}</div>
  </section>;
}

function SnapshotCard({ card }) {
  const facts = card.facts || [card];
  return <article className="snapshot-fact">
    <h4>{card.title}</h4>
    {facts.map((fact, index) => <div className="snapshot-value" key={`${fact.value}-${index}`}>
      <strong>{fact.value}</strong>
      {fact.detail && <p>{fact.detail}</p>}
      {fact.time != null && <small>Video {formatTime(fact.time)}</small>}
      {fact.establishedTime != null && <small>Established {formatTime(fact.establishedTime)}</small>}
    </div>)}
  </article>;
}
