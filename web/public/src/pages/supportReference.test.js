import assert from "node:assert/strict";
import test from "node:test";
import { looksLikeReference } from "./supportReference.js";

test("support references are recognized with or without the prefix", () => {
  assert.equal(looksLikeReference("S3-K7M4P9Q2"), true);
  assert.equal(looksLikeReference(" s3-k7m4p9q2 "), true);
  assert.equal(looksLikeReference("K7M4P9Q2"), true);
});

test("ids, emails and confusable characters are not references", () => {
  assert.equal(looksLikeReference("0f3c9a5e7b2d4c6e8a1b3d5f7092e4c6"), false);
  assert.equal(looksLikeReference("rin@example.com"), false);
  assert.equal(looksLikeReference("S3-K7M4P9Q0"), false);
  assert.equal(looksLikeReference("S3-K7M4P9QI"), false);
  assert.equal(looksLikeReference(""), false);
});
