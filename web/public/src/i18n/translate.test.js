import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { normalizeLocale, translate } from "./translate.js";

const load = (name) => JSON.parse(readFileSync(new URL(name, import.meta.url), "utf8"));
const resources = { en: load("./en.json"), ja: load("./ja.json") };

test("English and Japanese resolve", () => {
  assert.equal(translate(resources, "en", "submit.submit"), "Submit Match");
  assert.notEqual(translate(resources, "ja", "submit.submit"), "Submit Match");
  assert.equal(translate(resources, "ja", "common.deaths"), resources.ja["common.deaths"]);
  assert.equal(
    translate(resources, "en", "submit.choose_video", { mb: 500 }),
    "Choose video (500MB max)",
  );
});

test("a missing Japanese key falls back to English", () => {
  const partial = { en: { "only.en": "Hello {name}" }, ja: {} };
  assert.equal(translate(partial, "ja", "only.en", { name: "Rin" }), "Hello Rin");
});

test("a missing key returns the key", () => {
  assert.equal(translate(resources, "ja", "no.such.key"), "no.such.key");
});

test("unknown locales use English", () => {
  assert.equal(normalizeLocale("fr"), "en");
  assert.equal(normalizeLocale("ja"), "ja");
});
