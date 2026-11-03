/** Presentation of exported objective facts. No state or progress inference. */
import { formatTime } from "./labels.js";

export const MODE_LABELS = {
  splat_zones: "Splat Zones", tower_control: "Tower Control",
  rainmaker: "Rainmaker", clam_blitz: "Clam Blitz",
};

const SLOTS = [["at_anchor", "At anchor"], ["pre_death", "Pre-death"], ["lookback", "Lookback"]];
export const humanize = (value) => value == null ? "Unknown" : String(value).replaceAll("_", " ");
export const countText = (value) => value == null ? "Unknown" : String(value);
export const penaltyText = (value) => value == null ? "Unknown" : `+${value}`;

function countFields(sample, penalties = false) {
  const value = sample.score || sample;
  const rows = [
    ["Ally remaining", countText(value.ally_remaining)],
    ["Opponent remaining", countText(value.opponent_remaining)],
  ];
  if (penalties) rows.push(
    ["Ally penalty", penaltyText(value.ally_penalty)],
    ["Opponent penalty", penaltyText(value.opponent_penalty)],
  );
  for (const [key, label] of [
    ["ally_score_quality", "Ally count quality"], ["opponent_score_quality", "Opponent count quality"],
    ["ally_penalty_quality", "Ally penalty quality"], ["opponent_penalty_quality", "Opponent penalty quality"],
  ]) if (value[key] != null) rows.push([label, humanize(value[key])]);
  if (value.association_basis) rows.push(["Team association", humanize(value.association_basis)]);
  return rows;
}

function section(title, stream, fields, fresh = false) {
  return {
    title,
    samples: SLOTS.map(([key, label]) => {
      const sample = stream?.[key];
      const values = sample?.score || sample;
      const hasFreshValue = values && ["ally_remaining", "opponent_remaining", "ally_penalty", "opponent_penalty"]
        .some((field) => values[field] != null);
      return {
        key, label, available: sample != null,
        time: sample?.sample_video_time ?? sample?.video_time ?? null,
        quality: fresh && hasFreshValue ? "Fresh observation" : sample?.quality ?? null,
        establishedTime: sample?.state_established_video_time ?? null,
        fields: sample ? fields(sample) : [],
        supports: sample ? supportingEvidence(sample) : [],
      };
    }),
  };
}

export function objectiveSections(objective) {
  if (!objective) return [];
  const state = (s) => [["State", humanize(s.state)]];
  switch (objective.mode) {
    case "splat_zones": return [
      section("Counts and penalties", objective.score, (s) => countFields(s, true)),
      section("Zone control", objective.control, state),
    ];
    case "tower_control": return [section("Tower control", objective.control, state)];
    case "rainmaker": return [
      section("Remaining counts", objective.counts, (s) => countFields(s), true),
      section("Possession", objective.possession, state),
      section("Checkpoint", objective.checkpoint, (s) => [
        ["Ally checkpoint", humanize(s.state?.ally)],
        ["Opponent checkpoint", humanize(s.state?.opponent)],
        ...["ally", "opponent"].flatMap((team) => {
          const observedAt = s.state?.[`${team}_evidence`]?.first_video_time;
          return observedAt == null ? [] : [[`${humanize(team)} breakthrough observed`, formatTime(observedAt)]];
        }),
      ]),
    ];
    case "clam_blitz": return [
      section("Primary counts and penalties", objective.score, (s) => countFields(s, true), true),
    ];
    default: return [];
  }
}

export function progressRows(progress) {
  return (progress?.observations || []).map((entry) => {
    const obs = entry.objective;
    if (!obs) return null;
    return {
      time: obs.video_time, team: humanize(obs.team), value: countText(obs.original_value),
      label: obs.value_label || obs.value_kind,
      state: humanize(obs.progress_state_from_previous),
      amount: obs.normalized_progress_amount == null ? "Unknown" : String(obs.normalized_progress_amount),
      supports: supportingEvidence({ video_time: obs.video_time, evidence_ids: obs.objective_evidence_ids }),
    };
  }).filter(Boolean);
}

/** Keep direct cue support separate from the frame sampling a held state. */
export function supportingEvidence(sample) {
  const out = [];
  const add = (id, source, time, cue, role = "supporting") => out.push({
    evidenceId: id || null, sourceFrame: source || null, frameId: source?.frame_id || null,
    videoTime: source?.timestamp ?? time ?? null, role,
    cue: cue?.cue ?? cue?.cue_type ?? cue?.kind ?? null,
    language: cue?.language ?? null, template: cue?.template_name ?? null,
  });
  const group = (obs) => {
    if (!obs) return;
    for (const item of obs.samples || []) {
      const cue = item.cue || item.reading || { cue: obs.cue, language: item.language };
      add(item.evidence_id, item.source_frame, item.video_time, cue);
    }
  };
  const evidence = sample.evidence;
  if (evidence) {
    add(evidence.detector_evidence_id || evidence.detector_evidence_ids?.[0],
      evidence.source_frame, evidence.video_time,
      evidence.cue || (evidence.initialization ? { cue: evidence.initialization } : null), "establishing");
    group(evidence.cue_observation);
    group(evidence.took_observation);
    group(evidence.opponent_lost_observation);
    for (const id of evidence.detector_evidence_ids || []) add(id, null, evidence.video_time);
  }
  for (const team of ["ally", "opponent"]) group(sample.state?.[`${team}_evidence`]);
  for (const id of sample.evidence_ids || []) add(id, sample.source_frame, sample.video_time);
  if (sample.score?.evidence_id) add(sample.score.evidence_id, sample.source_frame, sample.video_time);
  for (const id of sample.association?.calibration_frame_ids || []) {
    out.push({ evidenceId: null, frameId: id, videoTime: sample.association.calibration_time,
      sourceFrame: null, role: "team calibration" });
  }
  if (sample.source_frame && !out.some((ref) => ref.frameId === sample.source_frame.frame_id)) {
    add(null, sample.source_frame, sample.sample_video_time ?? sample.video_time, null, "sample");
  }
  const merged = new Map();
  for (const ref of out) {
    const key = ref.evidenceId || `${ref.role}:${ref.frameId}:${ref.videoTime}`;
    const previous = merged.get(key);
    if (!previous) merged.set(key, ref);
    else for (const field of ["sourceFrame", "frameId", "cue", "language", "template", "videoTime"]) {
      if (previous[field] == null) previous[field] = ref[field];
    }
  }
  return [...merged.values()];
}

/** Exact identities only. Missing records never resolve to a nearby image. */
export function resolveObjectiveEvidence(reference, observations) {
  const byId = reference.evidenceId
    ? observations.filter((o) => o.id === reference.evidenceId
      && (!reference.frameId || o.frame_id === reference.frameId)) : [];
  if (byId.length === 1) return { kind: "detector", observation: byId[0] };
  const byFrame = reference.frameId
    ? observations.filter((o) => o.frame_id === reference.frameId) : [];
  if (byFrame.length) return { kind: "frame", observation: byFrame[0] };
  return { kind: "missing", observation: null };
}
