import test from "node:test";
import assert from "node:assert/strict";
import { buildSync } from "esbuild";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { objectiveSections, progressRows, resolveObjectiveEvidence, supportingEvidence } from "./objectiveEvidence.js";
import { tilePasses } from "./cadence.js";
import { scenarioSnapshot } from "./scenarioSnapshot.js";

// Render the real component without a browser or additional test dependencies.
function loadComponent(path) {
  const compiled = buildSync({ entryPoints: [fileURLToPath(new URL(path, import.meta.url))],
    bundle: true, write: false, platform: "node", format: "cjs", packages: "external", jsx: "automatic" });
  const loaded = { exports: {} };
  new Function("require", "module", "exports", compiled.outputFiles[0].text)(
    createRequire(import.meta.url), loaded, loaded.exports,
  );
  return loaded.exports;
}
const { ObjectiveEvidence } = loadComponent("./ObjectiveEvidence.jsx");
const { ScenarioSnapshot } = loadComponent("./ScenarioSnapshot.jsx");
const { EvidenceSequence } = loadComponent("./ScenarioEvidenceSequence.jsx");
const render = (objective) => renderToStaticMarkup(React.createElement(ObjectiveEvidence, { objective }));
const renderSnapshot = (scenario) => renderToStaticMarkup(React.createElement(ScenarioSnapshot, { scenario }));
const frame = { frame_id: "f5", timestamp: 5, source_frame_index: 300, source_pts: 900, source_time_base_num: 1, source_time_base_den: 180 };
const held = {
  sample_video_time: 10, state: "ally_control", quality: "held", state_established_video_time: 5,
  source_frame: { frame_id: "f10", timestamp: 10 },
  evidence: { video_time: 5, source_frame: frame, detector_evidence_id: "cue5", cue: { cue_type: "ally_took", language: "en" } },
};
const progress = { observations: [{ objective: {
  video_time: 10, team: "ally", original_value: 68, value_label: "ally remaining count",
  progress_state_from_previous: "progress", normalized_progress_amount: 4, objective_evidence_ids: ["count10"],
} }] };

test("old artifacts clearly show unavailable context", () => {
  assert.match(render(null), /Objective context unavailable in this artifact/);
});

test("Zones renders persisted values and quality without using raw manifest state", () => {
  const objective = { mode: "splat_zones", score: { at_anchor: {
    video_time: 9.5, ally_remaining: 72, opponent_remaining: 46,
    ally_penalty: 3, opponent_penalty: null, ally_score_quality: "observed", opponent_penalty_quality: "not_shown",
  } }, control: { at_anchor: { video_time: 10, state: "ally_control", quality: "observed" } }, progress };
  const html = render(objective);
  for (const text of ["Splat Zones", "72", "46", "+3", "not shown", "observed", "0:09.5", "Progress observations (1)"]) assert.ok(html.includes(text));
  assert.ok(!html.includes("+0"));
});

test("Tower shows held state and original establishing time, with no progress", () => {
  const html = render({ mode: "tower_control", control: { at_anchor: held }, progress: null });
  for (const text of ["Tower control", "ally control", "held", "0:10.0", "State established at video", "0:05.0", "Unavailable."]) assert.ok(html.includes(text));
  const support = supportingEvidence(held);
  assert.equal(support[0].role, "establishing");
  assert.equal(support[0].evidenceId, "cue5");
  assert.equal(support[0].sourceFrame.source_pts, 900);
  assert.equal(support[0].language, "en");
  assert.equal(support[1].role, "sample");
});

test("Rainmaker possession quality and checkpoint unknown remain independent", () => {
  const obj = { mode: "rainmaker", counts: { at_anchor: { video_time: 10, ally_remaining: 72 } },
    possession: { at_anchor: { ...held, state: "opponent_possession", quality: "observed" } },
    checkpoint: { at_anchor: { sample_video_time: 10, state: { ally: "cleared", opponent: "unknown" } } } };
  const html = render(obj);
  for (const text of ["Rainmaker", "opponent possession", "observed", "cleared", "unknown"]) assert.ok(html.includes(text));
  assert.ok(!html.includes("uncleared"));
  const unreadable = render({ mode: "rainmaker", counts: { at_anchor: { video_time: 10,
    ally_remaining: null, opponent_remaining: null, evidence_ids: [] } } });
  assert.ok(!unreadable.includes("Fresh observation"));
});

test("Clam fresh samples preserve zero counts, missing penalties, and persisted progress", () => {
  const obj = { mode: "clam_blitz", score: { at_anchor: { video_time: 9.5, source_frame: frame,
    score: { ally_remaining: 0, opponent_remaining: 32, ally_penalty: null, opponent_penalty: 19, evidence_id: "clam9" } } }, progress };
  const html = render(obj);
  for (const text of ["Clam Blitz", "Fresh observation", "0:09.5", "+19", "Unknown", "Progress observations (1)"]) assert.ok(html.includes(text));
  assert.ok(!html.includes("+0"));
  assert.ok(!html.includes("held"));
  assert.ok(!html.includes("State established"));
  assert.deepEqual(progressRows(progress).map((r) => [r.state, r.amount, r.value]), [["progress", "4", "68"]]);
  assert.equal(objectiveSections(obj)[0].samples[0].supports[0].evidenceId, "clam9");
  assert.equal(objectiveSections(obj)[0].samples[1].available, false);
});

test("progress displays adapter classifications without calculating from native values", () => {
  const rows = progressRows({ observations: [
    { objective: { original_value: 60, progress_state_from_previous: "unknown", normalized_progress_amount: null } },
    { objective: { original_value: 58, progress_state_from_previous: "no_progress", normalized_progress_amount: 0 } },
  ] });
  assert.deepEqual(rows.map((r) => [r.state, r.amount]), [["unknown", "Unknown"], ["no progress", "0"]]);
});

test("evidence navigation resolves exact IDs, exact frames, and never substitutes nearby", () => {
  const observations = [
    { id: "nearby", frame_id: "near", timestamp: 5.01 },
    { id: "cue5", frame_id: "f5", timestamp: 5, positive: true, detector: "tower_control" },
  ];
  const ref = supportingEvidence(held)[0];
  assert.equal(resolveObjectiveEvidence(ref, observations).observation.id, "cue5");
  assert.equal(resolveObjectiveEvidence({ ...ref, evidenceId: "absent" }, observations).kind, "frame");
  assert.equal(resolveObjectiveEvidence({ evidenceId: "absent", frameId: "absent", videoTime: 5 }, observations).kind, "missing");
  assert.equal(resolveObjectiveEvidence({ evidenceId: "cue5", frameId: "wrong" }, observations).kind, "missing");
  assert.ok(tilePasses({ readings: [observations[1]] }, "tower_control", 0));
});

test("grouped cue support retains language and template metadata", () => {
  const refs = supportingEvidence({ evidence: { source_frame: frame, video_time: 5, detector_evidence_ids: ["ja"],
    cue_observation: { cue: "ally_acquired_rainmaker", samples: [{ video_time: 5, source_frame: frame, evidence_id: "ja",
      cue: { cue: "ally_acquired_rainmaker", language: "ja", template_name: "ally_acquired" } }] } } });
  assert.equal(refs.length, 1);
  assert.equal(refs[0].language, "ja");
  assert.equal(refs[0].template, "ally_acquired");
  assert.equal(refs[0].videoTime, 5);
});

test("scenario snapshot uses the persisted death roster point and lifecycle facts", () => {
  const cards = scenarioSnapshot({ scenario_type: "death_episode", recovery: {
    death_time: 61, respawn_time: 68, active_again_time: 69,
  }, players: { at_death: { video_time: 61, ally_alive_count: 4, opponent_alive_count: 3, reliable: true },
    trajectory: [{}, {}] } });
  assert.ok(cards.some((card) => card.title === "Players" && card.value === "4v3" && card.detail.includes("Ally +1")));
  assert.ok(cards.some((card) => card.title === "Timeline" && card.value.includes("Death 1:01.0")
    && card.value.includes("Respawn 1:08.0") && card.value.includes("Active 1:09.0")));
});

test("snapshot summarizes all objective modes without changing unknowns or progress", () => {
  const cases = [
    [{ mode: "splat_zones", score: { at_anchor: { video_time: 2, ally_remaining: 62, opponent_remaining: 74 } },
      control: { at_anchor: { video_time: 2, state: "ally_control", quality: "observed" } },
      progress: { observations: [{ objective: { video_time: 2, progress_state_from_previous: "progress", normalized_progress_amount: 4 } }] } },
    ["62 – 74", "ally control", "Progress · progress", "+4 normalized units"]],
    [{ mode: "tower_control", control: { at_anchor: { sample_video_time: 3, state: "opponent_control", quality: "held" } }, progress: null }, ["opponent control", "held"]],
    [{ mode: "rainmaker", counts: { at_anchor: { video_time: 4, ally_remaining: 63, opponent_remaining: 41 } },
      possession: { at_anchor: { sample_video_time: 4, state: "opponent_possession", quality: "observed" } },
      checkpoint: { at_anchor: { sample_video_time: 4, state: { ally: "unknown", opponent: "unknown" } } } },
    ["63 – 41", "opponent possession", "ally unknown"]],
    [{ mode: "clam_blitz", score: { pre_death: { video_time: 5,
      score: { ally_remaining: 72, opponent_remaining: 48, ally_penalty: 12, opponent_penalty: null } } } }, ["72 – 48", "Ally +12"]],
  ];
  for (const [objective, expected] of cases) {
    const html = renderSnapshot({ scenario_type: "death_episode", objective });
    for (const text of expected) assert.ok(html.includes(text), `${objective.mode} should include ${text}`);
  }
  const clam = renderSnapshot({ scenario_type: "death_episode", objective: { mode: "clam_blitz",
    score: { pre_death: { video_time: 5, score: { ally_remaining: 72, opponent_remaining: 48,
      ally_penalty: null, opponent_penalty: null } } } } });
  assert.ok(!clam.includes("+0"));
  const tower = renderSnapshot({ scenario_type: "death_episode", objective: { mode: "tower_control",
    control: { pre_death: { sample_video_time: 3, state: "unknown", quality: "unknown" } }, progress: null } });
  assert.ok(tower.includes("unknown"));
  assert.ok(!tower.includes("Progress"));
  const unmatchedProgress = renderSnapshot({ scenario_type: "death_episode", objective: {
    mode: "clam_blitz", score: { pre_death: { video_time: 5, score: { ally_remaining: 72, opponent_remaining: 48 } } },
    progress: { observations: [{ objective: { video_time: 4.5, progress_state_from_previous: "progress", normalized_progress_amount: 2 } }] },
  } });
  assert.ok(!unmatchedProgress.includes("Progress progress"));
});

test("map and special snapshot keep absent evidence unavailable", () => {
  const html = renderSnapshot({ scenario_type: "death_episode", map: {
    map_check_status: "observed", ink: { nearest_before_anchor: { video_time: 9, ally_share_of_detected_ink: 0.66 } },
  }, special: { state_at_anchor: "not_observed", observations: [{ video_time: 9 }] } });
  assert.ok(html.includes("Checked before death"));
  assert.ok(html.includes("Ally share of detected ink 66%"));
  assert.ok(html.includes("Special"));
  assert.ok(html.includes("Unavailable"));
  assert.ok(!html.includes("Not ready"));
});

test("repetitive player and special streams are collapsed behind explicit controls", () => {
  const scenario = { scenario_type: "death_episode", players: {
    at_death: { video_time: 9, ally_alive_count: 4, opponent_alive_count: 3, reliable: true },
    trajectory: [{ video_time: 8, ally_alive_count: 4, opponent_alive_count: 4 },
      { video_time: 9, ally_alive_count: 4, opponent_alive_count: 3 }],
  }, special: { state_at_anchor: "not_ready", nearest_before_anchor: { video_time: 9 },
    observations: [{ video_time: 8.5, fill_fraction: 0.5 }, { video_time: 9, fill_fraction: 0.4 }] } };
  const players = loadComponent("./ScenarioEvidenceSequence.jsx").playerSequenceSummary(scenario);
  const special = loadComponent("./ScenarioEvidenceSequence.jsx").specialSequenceSummary(scenario.special);
  assert.match(players, /4v3 at death/);
  assert.match(players, /2 roster samples/);
  assert.match(special, /Not ready/);
  assert.match(special, /2 observations/);
  const html = renderToStaticMarkup(React.createElement(EvidenceSequence, { title: "Special gauge", summary: special,
    count: 2, disclosureTitle: "Show observations", value: scenario.special }));
  assert.ok(html.includes("Show observations (2)"));
  assert.ok(html.includes('aria-expanded="false"'));
  assert.ok(!html.includes("fill_fraction"));
  const playerHtml = renderToStaticMarkup(React.createElement(EvidenceSequence, { title: "Players", summary: players,
    count: 2, disclosureTitle: "Show timeline", value: scenario.players }));
  assert.ok(playerHtml.includes("Show timeline (2)"));
  assert.ok(playerHtml.includes('aria-expanded="false"'));
  assert.ok(!playerHtml.includes("opponent_alive_count"));
});

test("older sparse scenarios render a quiet nonempty snapshot", () => {
  const html = renderSnapshot({ scenario_type: "map_check", map: {}, players: null, objective: null, special: null });
  assert.ok(html.includes("Scenario snapshot"));
  assert.ok(html.includes("Map overlay recorded"));
  assert.ok(!html.includes("null"));
});

test("engagement and map-check summaries show only their existing scenario facts", () => {
  const engagement = renderSnapshot({ scenario_type: "engagement", combat: { splat_count: 2,
    first_splat_time: 14, last_splat_time: 16 }, relations: { leads_to_death_episode_id: "death_episode:17.0" } });
  assert.ok(engagement.includes("2 splats"));
  assert.ok(engagement.includes("First 0:14.0"));
  assert.ok(engagement.includes("Leads to death episode"));
  const mapCheck = renderSnapshot({ scenario_type: "map_check", map: { map_check_count: 1,
    ink: { nearest_before_anchor: { video_time: 20, ally_share_of_detected_ink: 0.5 } } } });
  assert.ok(mapCheck.includes("Map overlay recorded"));
  assert.ok(mapCheck.includes("Ally share of detected ink 50%"));
});
