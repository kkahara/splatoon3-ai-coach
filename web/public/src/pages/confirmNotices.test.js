import assert from "node:assert/strict";
import test from "node:test";
import { safeReviewPath } from "./confirmNotices.js";

test("only relative review paths are accepted after confirmation", () => {
  assert.equal(safeReviewPath("/review/abc"), "/review/abc");
  assert.equal(safeReviewPath("https://evil.example/review/abc"), null);
  assert.equal(safeReviewPath("/review/../admin"), null);
  assert.equal(safeReviewPath("/coaching/1"), null);
  assert.equal(safeReviewPath("/review/abc/extra"), null);
  assert.equal(safeReviewPath(null), null);
});
