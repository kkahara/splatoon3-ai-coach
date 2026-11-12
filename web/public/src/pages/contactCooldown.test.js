import assert from "node:assert/strict";
import test from "node:test";
import { cooldownMinutes, cooldownUntil } from "./contactCooldown.js";

test("a reported cooldown becomes a clock time", () => {
  assert.equal(cooldownUntil(90, 1000), 91000);
  assert.equal(cooldownUntil(null, 1000), 0);
  assert.equal(cooldownUntil(0, 1000), 0);
  assert.equal(cooldownUntil("nope", 1000), 0);
});

test("remaining minutes round up and end at zero", () => {
  assert.equal(cooldownMinutes(cooldownUntil(900, 0), 0), 15);
  assert.equal(cooldownMinutes(cooldownUntil(61, 0), 0), 2);
  assert.equal(cooldownMinutes(cooldownUntil(1, 0), 0), 1);
  assert.equal(cooldownMinutes(5000, 5000), 0);
  assert.equal(cooldownMinutes(0, 5000), 0);
});
