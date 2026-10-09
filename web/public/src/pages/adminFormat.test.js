import assert from "node:assert/strict";
import test from "node:test";
import { formatRate, sampleNote } from "./adminFormat.js";

test("small comparison groups are labeled insufficient", () => {
  assert.equal(sampleNote(3), "n=3 — insufficient sample for stable comparison");
  assert.equal(sampleNote(10), "n=10");
});

test("rates render as percentages and stay blank when absent", () => {
  assert.equal(formatRate(1 / 3), "33.3%");
  assert.equal(formatRate(null), "—");
});
