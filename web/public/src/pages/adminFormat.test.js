import assert from "node:assert/strict";
import test from "node:test";
import { formatAverage, formatEstimatedCost, formatRate, sampleNote } from "./adminFormat.js";

test("small comparison groups are labeled insufficient", () => {
  assert.equal(sampleNote(3), "n=3 — insufficient sample for stable comparison");
  assert.equal(sampleNote(10), "n=10");
});

test("rates render as percentages and stay blank when absent", () => {
  assert.equal(formatRate(1 / 3), "33.3%");
  assert.equal(formatRate(null), "—");
});

test("averages stay blank when a group has no completed submissions", () => {
  assert.equal(formatAverage(null), "—");
  assert.equal(formatAverage(1.5), "1.50");
});

test("estimated cost stays blank when the model has no price", () => {
  assert.equal(formatEstimatedCost(null), "—");
  assert.equal(formatEstimatedCost(0.125), "$0.1250");
});
