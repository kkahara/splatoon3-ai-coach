import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { translate } from "../i18n/translate.js";
import { formatWhen, historyCard } from "./historyCard.js";

const load = (name) =>
  JSON.parse(readFileSync(new URL(`../i18n/${name}`, import.meta.url), "utf8"));
const resources = { en: load("en.json"), ja: load("ja.json") };
const translator = (locale) => (key, params) => translate(resources, locale, key, params);

const STAMP = "2026-10-21T23:14:20Z";

function item(update = {}) {
  return {
    id: "abc123",
    created_at: STAMP,
    status: "complete",
    outcome: "coached",
    battle_mode_id: "splat_zones",
    stage_id: "hagglefish_market",
    moment_count: 2,
    ...update,
  };
}

function card(update, locale = "en") {
  return historyCard(item(update), translator(locale), locale);
}

function assertCleanSeparators(text) {
  assert.ok(!text.startsWith("·") && !text.startsWith(" ·"), text);
  assert.ok(!text.endsWith("·") && !text.endsWith("· "), text);
  assert.ok(!text.includes("· ·") && !text.includes("··"), text);
}

test("a fully populated card shows mode, stage, count and date", () => {
  const en = card({});
  assert.equal(en.title, "Splat Zones · Hagglefish Market");
  assert.equal(en.status, "Complete");
  assert.equal(en.meta, `2 detected moments · ${formatWhen(STAMP, "en")}`);
  const ja = card({}, "ja");
  assert.equal(ja.title, "ガチエリア · ヤガラ市場");
  assert.equal(ja.status, "完了");
  assert.equal(ja.meta, `検出場面 2件 · ${formatWhen(STAMP, "ja")}`);
});

test("a partially populated card omits what is missing", () => {
  const modeOnly = card({ stage_id: null });
  assert.equal(modeOnly.title, "Splat Zones");
  const stageOnly = card({ battle_mode_id: null });
  assert.equal(stageOnly.title, "Hagglefish Market");
  const one = card({ moment_count: 1 });
  assert.equal(one.meta, `1 detected moment · ${formatWhen(STAMP, "en")}`);
});

test("a completed analysis with no moments is complete, with the outcome in the meta line", () => {
  for (const update of [{ outcome: "no_deaths", moment_count: 0 }, { moment_count: 0 }]) {
    const en = card(update);
    assert.equal(en.status, "Complete");
    assert.ok(en.meta.startsWith("No coaching moments · "), en.meta);
    const ja = card(update, "ja");
    assert.equal(ja.status, "完了");
    assert.ok(ja.meta.startsWith("コーチング場面なし · "), ja.meta);
  }
});

test("a legacy analysis without match facts falls back to a generic title", () => {
  const legacy = card({ battle_mode_id: undefined, stage_id: undefined, moment_count: undefined });
  assert.equal(legacy.title, "Match");
  assert.equal(legacy.meta, formatWhen(STAMP, "en"));
  assert.equal(card({ battle_mode_id: null, stage_id: null }, "ja").title, "試合");
});

test("an unknown id is omitted, never shown raw", () => {
  const unknown = card({ stage_id: "new_stage_2027" });
  assert.equal(unknown.title, "Splat Zones");
  assert.equal(card({ battle_mode_id: "x", stage_id: "y" }).title, "Match");
});

test("unfinished submissions show status only, without a moment phrase", () => {
  const cases = [
    ["processing", "In progress", "処理中"],
    ["queued", "In progress", "処理中"],
    ["failed", "Failed", "失敗"],
    ["expired", "Expired", "期限切れ"],
  ];
  for (const [status, en, ja] of cases) {
    const update = { status, outcome: null, moment_count: null };
    assert.equal(card(update).status, en);
    assert.equal(card(update, "ja").status, ja);
    assert.equal(card(update).meta, formatWhen(STAMP, "en"));
  }
});

test("each card links to its own review", () => {
  assert.equal(card({}).href, "/coaching/abc123");
  assert.equal(card({ status: "processing" }).href, "/coaching/abc123");
});

test("no combination produces malformed separators", () => {
  const modes = ["splat_zones", null, "unknown"];
  const stages = ["hagglefish_market", null, "unknown"];
  const counts = [0, 1, 5, null];
  const stamps = [STAMP, "", "not a date"];
  for (const locale of ["en", "ja"]) {
    for (const battle_mode_id of modes) {
      for (const stage_id of stages) {
        for (const moment_count of counts) {
          for (const created_at of stamps) {
            const result = card({ battle_mode_id, stage_id, moment_count, created_at }, locale);
            assertCleanSeparators(result.title);
            assertCleanSeparators(result.meta);
            assert.ok(result.title.length > 0);
          }
        }
      }
    }
  }
});

test("dates drop seconds and fall back to the raw value", () => {
  assert.ok(!formatWhen(STAMP, "en").includes(":20"), formatWhen(STAMP, "en"));
  assert.ok(formatWhen(STAMP, "ja").includes("2026"), formatWhen(STAMP, "ja"));
  assert.equal(formatWhen("not a date", "en"), "not a date");
  assert.equal(formatWhen("", "en"), "");
});

test("history heading strings", () => {
  assert.equal(translate(resources, "en", "coaching.title"), "Coaching History");
  assert.equal(translate(resources, "ja", "coaching.title"), "コーチング履歴");
});

test("every mode and stage name exists in both languages", () => {
  const names = (table) => Object.keys(table).filter((key) => key.startsWith("match."));
  assert.deepEqual(names(resources.en).sort(), names(resources.ja).sort());
  assert.equal(names(resources.en).filter((key) => key.startsWith("match.mode.")).length, 5);
  assert.equal(names(resources.en).filter((key) => key.startsWith("match.stage.")).length, 25);
});
