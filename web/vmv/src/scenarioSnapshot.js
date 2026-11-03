/** Compact summaries from persisted ScenarioContext facts only. */
import { formatTime } from "./labels.js";
import { countText, humanize, MODE_LABELS } from "./objectiveEvidence.js";

const slotFor = (scenarioType) => scenarioType === "death_episode" ? "pre_death" : "at_anchor";

function firstSample(stream, scenarioType) {
  if (!stream) return null;
  const preferred = slotFor(scenarioType);
  return stream[preferred] || (preferred === "pre_death" ? stream.at_anchor : null) || null;
}

function objectiveSummary(objective, scenarioType) {
  if (!objective) return null;
  const choose = (stream) => firstSample(stream, scenarioType);
  const countPair = (sample) => {
    if (!sample) return null;
    const values = sample.score || sample;
    const ally = countText(values.ally_remaining);
    const opponent = countText(values.opponent_remaining);
    if (ally === "Unknown" && opponent === "Unknown") return null;
    return `${ally} – ${opponent}`;
  };
  const suffix = (sample) => sample?.sample_video_time ?? sample?.video_time ?? null;
  const countQualifier = (sample) => {
    const values = sample?.score || sample || {};
    const parts = [
      values.ally_score_quality != null ? `Ally ${humanize(values.ally_score_quality)}` : null,
      values.opponent_score_quality != null ? `Opponent ${humanize(values.opponent_score_quality)}` : null,
      sample?.quality != null ? humanize(sample.quality) : null,
    ].filter(Boolean);
    return [...new Set(parts)].join(" · ") || null;
  };
  const stateFact = (sample) => ({
    value: humanize(sample.state),
    detail: sample.quality != null ? humanize(sample.quality) : null,
    time: suffix(sample),
    establishedTime: sample.state_established_video_time ?? null,
  });
  const rows = [];
  if (objective.mode === "splat_zones") {
    const score = choose(objective.score);
    const control = choose(objective.control);
    const pair = countPair(score);
    if (score && (pair || score.ally_penalty != null || score.opponent_penalty != null)) rows.push({ value: pair || "Counts unavailable", detail: ["Ally – opponent remaining",
      ...(score.ally_penalty != null || score.opponent_penalty != null
        ? [`Ally penalty ${score.ally_penalty == null ? "Unknown" : `+${score.ally_penalty}`} · Opponent penalty ${score.opponent_penalty == null ? "Unknown" : `+${score.opponent_penalty}`}`] : []),
      countQualifier(score)].filter(Boolean).join(" · "), time: suffix(score) });
    if (control) rows.push(stateFact(control));
  } else if (objective.mode === "tower_control") {
    const control = choose(objective.control);
    if (control) rows.push(stateFact(control));
  } else if (objective.mode === "rainmaker") {
    const counts = choose(objective.counts);
    const pair = countPair(counts);
    if (counts) rows.push({ value: pair || "Counts unavailable", detail: ["Ally – opponent remaining", countQualifier(counts)].filter(Boolean).join(" · "), time: suffix(counts) });
    const possession = choose(objective.possession);
    if (possession) rows.push(stateFact(possession));
    const checkpoint = choose(objective.checkpoint);
    if (checkpoint) rows.push({ value: ["ally", "opponent"].map((team) => `${humanize(team)} ${humanize(checkpoint.state?.[team])}`).join(" · "),
      detail: "Checkpoint state", time: checkpoint.sample_video_time });
  } else if (objective.mode === "clam_blitz") {
    const score = choose(objective.score);
    const pair = countPair(score);
    if (score && (pair || score.score.ally_penalty != null || score.score.opponent_penalty != null)) {
      const values = score.score;
      const penalties = [];
      if (values.ally_penalty != null) penalties.push(`Ally +${values.ally_penalty}`);
      if (values.opponent_penalty != null) penalties.push(`Opponent +${values.opponent_penalty}`);
      rows.push({ value: pair || "Counts unavailable", detail: [penalties.length ? penalties.join(" · ") : "Ally – opponent remaining", countQualifier(score)].filter(Boolean).join(" · "), time: score.video_time });
    }
  }
  for (const entry of objective.progress?.observations || []) {
    const observation = entry.objective;
    if (!observation) continue;
    const team = observation.team != null ? `${humanize(observation.team)} progress` : "Progress";
    const state = humanize(observation.progress_state_from_previous);
    const amount = observation.normalized_progress_amount;
    rows.push({
      value: `${team} · ${state}`,
      detail: amount == null ? null : `${amount > 0 ? "+" : ""}${amount} normalized units`,
      time: observation.video_time,
    });
  }
  return { mode: MODE_LABELS[objective.mode] || objective.mode, rows };
}

export function scenarioSnapshot(scenario) {
  if (!scenario) return [];
  const cards = [];
  const isDeath = scenario.scenario_type === "death_episode";
  const players = scenario.players;
  const playerPoint = isDeath ? players?.at_death : players?.at_anchor;
  if (playerPoint?.reliable !== false) {
    const point = playerPoint;
    if (point) {
      const delta = point.ally_alive_count - point.opponent_alive_count;
      cards.push({ title: "Players", value: `${point.ally_alive_count}v${point.opponent_alive_count}`,
        detail: `${delta > 0 ? `Ally +${delta}` : delta < 0 ? `Opponent +${Math.abs(delta)}` : "Even"} · ${isDeath ? "At death" : "At anchor"}`,
        time: point.video_time });
    }
  }
  const objective = objectiveSummary(scenario.objective, scenario.scenario_type);
  if (objective?.rows.length) {
    cards.push({ title: `Objective · ${objective.mode}`, facts: objective.rows });
  }
  const map = scenario.map || {};
  let mapValue = null;
  if (isDeath) mapValue = mapCheckFact(map);
  else if (Number(map.map_checks_during_scenario) > 0) mapValue = `${map.map_checks_during_scenario} map check${map.map_checks_during_scenario === 1 ? "" : "s"}`;
  else if (scenario.scenario_type === "map_check") mapValue = "Map overlay recorded";
  const ink = map.ink?.nearest_before_anchor;
  const inkShare = ink?.ally_share_of_detected_ink;
  const inkText = inkShare == null ? null : `Ally share of detected ink ${Math.round(inkShare * 100)}%`;
  const mapWhileDead = isDeath && map.map_checked_while_dead != null
    ? map.map_checked_while_dead ? "Map checked while dead" : "No map check while dead observed" : null;
  if (mapValue || inkText || mapWhileDead) cards.push({ title: "Map", value: mapValue || mapWhileDead || inkText,
    detail: [mapValue && inkText, mapWhileDead].filter(Boolean).join(" · ") || null, time: ink?.video_time ?? null });

  const special = scenario.special;
  const specialState = special?.state_at_anchor;
  const specialReading = special?.nearest_before_anchor;
  const specialValue = specialState === "ready" ? "Ready" : specialState === "not_ready" ? "Not ready" : null;
  if (specialValue) cards.push({ title: "Special", value: specialValue,
    detail: specialReading?.video_time != null ? `Observed · ${formatTime(specialReading.video_time)}` : "Observed",
    time: specialReading?.video_time ?? null });
  else if (special && (specialState === "not_observed" || !specialReading)) cards.push({ title: "Special", value: "Unavailable" });

  const recovery = scenario.recovery || scenario.death_episode || {};
  const timeline = [];
  if (recovery.death_time != null) timeline.push(`Death ${formatTime(recovery.death_time)}`);
  if (recovery.respawn_time != null) timeline.push(`Respawn ${formatTime(recovery.respawn_time)}`);
  if (recovery.active_again_time != null) timeline.push(`Active ${formatTime(recovery.active_again_time)}`);
  if (scenario.scenario_type === "engagement") {
    const combat = scenario.combat || {};
    if (combat.splat_count != null || combat.first_splat_time != null || combat.last_splat_time != null) {
      cards.push({ title: "Combat", value: combat.splat_count == null ? "Splats unavailable" : `${combat.splat_count} splat${combat.splat_count === 1 ? "" : "s"}`,
        detail: [combat.first_splat_time == null ? null : `First ${formatTime(combat.first_splat_time)}`,
          combat.last_splat_time == null ? null : `Last ${formatTime(combat.last_splat_time)}`].filter(Boolean).join(" · ") || null });
    }
    if (combat.first_splat_time != null) timeline.push(`First splat ${formatTime(combat.first_splat_time)}`);
    if (combat.last_splat_time != null && combat.last_splat_time !== combat.first_splat_time) timeline.push(`Last splat ${formatTime(combat.last_splat_time)}`);
  }
  if (timeline.length) cards.push({ title: "Timeline", value: timeline.join(" · ") });
  const relations = scenario.relations || {};
  const relation = relations.leads_to_death_episode_id ? ["Leads to death episode", relations.leads_to_death_episode_id]
    : relations.follows_death_episode_id ? ["Follows death episode", relations.follows_death_episode_id]
      : relations.preceded_by_engagement_id ? ["Preceded by engagement", relations.preceded_by_engagement_id]
        : relations.next_engagement_id ? ["Next engagement", relations.next_engagement_id] : null;
  if (relation) cards.push({ title: "Relation", value: relation[0], detail: relation[1] });
  if (!cards.length) cards.push({ title: "Scenario snapshot", value: "No summary facts available" });
  return cards;
}

function mapCheckFact(map) {
  if (map.map_check_status === "observed" || (!map.map_check_status && map.map_check_before_death === true)) return "Checked before death";
  if (map.map_check_status === "not_observed") return "Not observed before death";
  if (map.map_check_status === "unknown") return "Unknown";
  return null;
}
