import assert from "node:assert/strict";
import test from "node:test";
import {
  contactBody,
  contactProblem,
  showPaymentWarning,
  showReviewLink,
} from "./contactForm.js";

const ready = {
  reason: "collaboration",
  name: "Rin",
  email: "rin@example.com",
  message: "I would like to help.",
  reference: "",
  token: "",
  devMode: true,
};

test("a technical problem shows the review link and hides the payment warning", () => {
  assert.equal(showReviewLink("technical"), true);
  assert.equal(showPaymentWarning("technical"), false);
  assert.equal(showReviewLink("collaboration"), false);
  assert.equal(showPaymentWarning("account_payment"), true);
});

test("a collaboration message omits a leftover review link", () => {
  const body = contactBody({
    token: "dev",
    reason: "collaboration",
    name: " Rin ",
    email: " rin@example.com ",
    message: " Hello ",
    reference: "https://coach.example/review/secret",
    trap: "",
  });
  assert.equal(body.reference, "");
  assert.equal(body.name, "Rin");
  assert.equal(body.email, "rin@example.com");
  assert.equal(body.message, "Hello");
});

test("a technical message keeps the review link", () => {
  const body = contactBody({
    token: "dev",
    reason: "technical",
    name: "Rin",
    email: "rin@example.com",
    message: "The review page failed.",
    reference: " https://coach.example/review/abc ",
    trap: "",
  });
  assert.equal(body.reference, "https://coach.example/review/abc");
});

test("dev mode can send without a Turnstile token", () => {
  assert.equal(contactProblem(ready), null);
  assert.equal(contactProblem({ ...ready, devMode: false, token: "" }), "contact.error_check");
});

test("an account or payment message still requires the usual fields", () => {
  assert.equal(
    contactProblem({ ...ready, reason: "account_payment", message: "   " }),
    "contact.error_message",
  );
});
