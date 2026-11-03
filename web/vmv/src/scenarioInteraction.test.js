import test, { afterEach } from "node:test";
import assert from "node:assert/strict";
import { buildSync } from "esbuild";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/" });
Object.assign(globalThis, {
  window: dom.window,
  document: dom.window.document,
  HTMLElement: dom.window.HTMLElement,
  Node: dom.window.Node,
  CSS: { escape: (value) => value },
  requestAnimationFrame: (callback) => setTimeout(callback, 0),
  IS_REACT_ACT_ENVIRONMENT: true,
});
Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
dom.window.HTMLElement.prototype.scrollIntoView = () => {};

const { cleanup, render, screen, waitFor } = await import("@testing-library/react");
const { default: userEvent } = await import("@testing-library/user-event");

function loadComponent(path) {
  const compiled = buildSync({
    entryPoints: [fileURLToPath(new URL(path, import.meta.url))],
    bundle: true,
    write: false,
    platform: "node",
    format: "cjs",
    packages: "external",
    jsx: "automatic",
  });
  const loaded = { exports: {} };
  new Function("require", "module", "exports", compiled.outputFiles[0].text)(
    createRequire(import.meta.url), loaded, loaded.exports,
  );
  return loaded.exports;
}

const { ScenarioEvidencePanel } = loadComponent("./pages/ScenarioStudio.jsx");
const { ObjectiveEvidence } = loadComponent("./ObjectiveEvidence.jsx");
const { ScenarioSnapshot } = loadComponent("./ScenarioSnapshot.jsx");
const { RunPage } = loadComponent("./pages/Run.jsx");

const scenarioA = {
  scenario_id: "death_episode:25.500",
  scenario_type: "death_episode",
  players: {
    at_death: { video_time: 25.5, ally_alive_count: 3, opponent_alive_count: 1, reliable: true },
    trajectory: [
      { video_time: 20.5, ally_alive_count: 4, opponent_alive_count: 2 },
      { video_time: 21, ally_alive_count: 4, opponent_alive_count: 1 },
      { video_time: 25.5, ally_alive_count: 3, opponent_alive_count: 1 },
    ],
  },
  special: {
    state_at_anchor: "not_ready",
    nearest_before_anchor: { video_time: 25 },
    observations: [
      { video_time: 24.5, fill_fraction: 0.714, ready: true, visible: true },
      { video_time: 25, fill_fraction: 0.81, ready: false, visible: true },
    ],
  },
};

const scenarioB = {
  ...scenarioA,
  scenario_id: "death_episode:31.000",
  special: {
    ...scenarioA.special,
    observations: [{ video_time: 31, fill_fraction: 0.333, ready: false, visible: true }],
  },
};

afterEach(() => cleanup());

test("Players and Special disclose readable rows before raw data and close again", async () => {
  const user = userEvent.setup();
  render(React.createElement(ScenarioEvidencePanel, { scenario: scenarioA }));

  const special = screen.getByRole("button", { name: /Show observations/ });
  assert.equal(special.getAttribute("aria-expanded"), "false");
  assert.equal(screen.queryByText("fill 0.714"), null);
  await user.click(special);
  assert.equal(screen.getByText("fill 0.714").textContent, "fill 0.714");
  assert.ok(screen.getByText(/ready yes · visible yes/));
  assert.equal(screen.queryByText(/fill_fraction/), null);
  await user.click(screen.getByRole("button", { name: "Raw data" }));
  assert.ok(screen.getByText(/"fill_fraction": 0.714/));
  await user.click(screen.getByRole("button", { name: "Raw data" }));
  assert.equal(screen.queryByText(/"fill_fraction": 0.714/), null);
  await user.click(special);
  assert.equal(screen.queryByText("fill 0.714"), null);

  const players = screen.getByRole("button", { name: /Show timeline/ });
  await user.click(players);
  assert.ok(screen.getByText("4v2 (Ally +2)"));
  assert.ok(screen.getByText("anchor"));
  await user.click(players);
  assert.equal(screen.queryByText("4v2 (Ally +2)"), null);
});

test("scenario navigation remounts evidence disclosures closed", async () => {
  const user = userEvent.setup();
  const view = render(React.createElement(ScenarioEvidencePanel, { key: scenarioA.scenario_id, scenario: scenarioA }));
  await user.click(screen.getByRole("button", { name: /Show observations/ }));
  assert.ok(screen.getByText("fill 0.714"));
  view.rerender(React.createElement(ScenarioEvidencePanel, { key: scenarioB.scenario_id, scenario: scenarioB }));
  const special = screen.getByRole("button", { name: /Show observations/ });
  assert.equal(special.getAttribute("aria-expanded"), "false");
  assert.equal(screen.queryByText("fill 0.333"), null);
});

test("snapshot keeps independently sampled objective facts, held provenance, and team progress", () => {
  render(React.createElement(ScenarioSnapshot, { scenario: {
    scenario_type: "death_episode",
    objective: {
      mode: "rainmaker",
      counts: { pre_death: { video_time: 30, ally_remaining: 63, opponent_remaining: 41 } },
      possession: { pre_death: {
        sample_video_time: 31,
        state: "opponent_possession",
        quality: "held",
        state_established_video_time: 20,
      } },
      progress: { observations: [
        { objective: { video_time: 30, team: "ally", progress_state_from_previous: "no_progress", normalized_progress_amount: 0 } },
        { objective: { video_time: 30, team: "opponent", progress_state_from_previous: "progress", normalized_progress_amount: 4 } },
      ] },
    },
  } }));
  assert.ok(screen.getByText("63 – 41"));
  assert.ok(screen.getAllByText("Video 0:30.0").length >= 1);
  assert.ok(screen.getByText("opponent possession"));
  assert.ok(screen.getByText("held"));
  assert.ok(screen.getByText("Video 0:31.0"));
  assert.ok(screen.getByText("Established 0:20.0"));
  assert.ok(screen.getByText("ally progress · no progress"));
  assert.ok(screen.getByText("opponent progress · progress"));
  assert.ok(screen.getByText("+4 normalized units"));
});

test("objective supporting-frame action forwards the exact reference", async () => {
  const user = userEvent.setup();
  const references = [];
  render(React.createElement(ObjectiveEvidence, {
    objective: { mode: "tower_control", control: { at_anchor: {
      sample_video_time: 10,
      state: "ally_control",
      quality: "held",
      state_established_video_time: 5,
      evidence: { detector_evidence_id: "cue5", video_time: 5, source_frame: { frame_id: "f5", timestamp: 5 } },
    } } },
    onViewEvidence: (reference) => references.push(reference),
  }));
  await user.click(screen.getByRole("button", { name: "Supporting evidence" }));
  await user.click(screen.getByRole("button", { name: "View supporting frame" }));
  assert.equal(references.length, 1);
  assert.equal(references[0].evidenceId, "cue5");
  assert.equal(references[0].frameId, "f5");
});

test("Ground Truth label changes still save through the React page", async () => {
  const calls = [];
  globalThis.fetch = async (path, options = {}) => {
    calls.push([path, options]);
    const payload = path.endsWith("/ground-truth")
      ? options.method === "PUT" ? JSON.parse(options.body) : { intervals: [] }
      : { view: {
        summary: { video_label: "run-1", duration_seconds: 10, frame_count: 1, warnings: [] },
        observations: [{ id: "death-1", timestamp: 2, detector: "death", positive: true, readings: [] }],
        detectors: ["death"], lifecycle_marks: [], lifecycle_segments: [], scenario_evidence: [], review_queue: [],
      } };
    return { ok: true, text: async () => JSON.stringify(payload), statusText: "OK" };
  };
  const user = userEvent.setup();
  render(React.createElement(MemoryRouter, { initialEntries: ["/runs/run-1"] },
    React.createElement(Routes, null, React.createElement(Route, { path: "/runs/:id", element: React.createElement(RunPage) })),
  ));
  await screen.findByRole("heading", { name: "run-1" });
  await user.click(screen.getByLabelText("Real death"));
  await waitFor(() => assert.ok(calls.some(([path, options]) => path.endsWith("/ground-truth") && options.method === "PUT")));
  const [, options] = calls.find(([path, options]) => path.endsWith("/ground-truth") && options.method === "PUT");
  assert.deepEqual(JSON.parse(options.body).intervals, [{ t0: 2, t1: 2, detector: "death", label: "real_death" }]);
});
