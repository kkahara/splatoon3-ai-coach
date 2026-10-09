/** Fetch helpers. They never log the review token. */

export async function loadConfig() {
  const response = await fetch("/api/config");
  if (!response.ok) {
    throw new Error("The coaching site is unavailable.");
  }
  return response.json();
}

export async function createSubmission(body) {
  const response = await fetch("/api/submissions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || "The submission was not accepted.");
  }
  return payload;
}

export async function uploadVideo(upload, file) {
  const response = await fetch(upload.url, {
    method: upload.method,
    headers: upload.headers,
    body: file,
  });
  if (!response.ok) {
    throw new Error("The video upload did not finish.");
  }
}

export async function markUploaded(token) {
  const response = await fetch(`/api/submissions/${encodeURIComponent(token)}/uploaded`, {
    method: "POST",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || "The video could not be checked.");
  }
  return payload;
}

async function read(response, fallback) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail;
    throw new Error(typeof detail === "string" ? detail : fallback);
  }
  return payload;
}

export async function loadAccount() {
  const response = await fetch("/api/me");
  if (response.status === 401 || response.status === 404 || response.status === 503) {
    return null;
  }
  return read(response, "The coaching site is unavailable.");
}

export async function registerAccount(body) {
  const response = await fetch("/api/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return read(response, "The account was not created.");
}

const verifyRequests = new Map();

export function verifyAccount(token) {
  if (!verifyRequests.has(token)) {
    const pending = fetch("/api/auth/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    })
      .then((response) => read(response, "That link is no longer valid."))
      .finally(() => verifyRequests.delete(token));
    verifyRequests.set(token, pending);
  }
  return verifyRequests.get(token);
}

export async function loginAccount(body) {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return read(response, "Email or password is wrong.");
}

export async function forgotPassword(email) {
  const response = await fetch("/api/auth/forgot", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  return read(response, "The reset link was not sent.");
}

export async function resetPassword(body) {
  const response = await fetch("/api/auth/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return read(response, "That link is no longer valid.");
}

export async function logoutAccount() {
  const response = await fetch("/api/auth/logout", { method: "POST" });
  return read(response, "Log out did not finish.");
}

export async function loadHistory() {
  const response = await fetch("/api/me/submissions");
  return read(response, "Past coaching is unavailable.");
}

export async function shareOwnedReview(id) {
  const response = await fetch(`/api/me/submissions/${encodeURIComponent(id)}/share`, {
    method: "POST",
  });
  const payload = await read(response, "The review link was not created.");
  return payload.review_path;
}

export async function sendFeedback({ token, id, body }) {
  const path = id
    ? `/api/me/submissions/${encodeURIComponent(id)}/feedback`
    : `/api/submissions/${encodeURIComponent(token)}/feedback`;
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body }),
  });
  return read(response, "Feedback was not saved.");
}

function localeQuery(locale) {
  return locale && locale !== "en" ? `?locale=${encodeURIComponent(locale)}` : "";
}

export async function loadOwnedSubmission(id, locale) {
  const response = await fetch(
    `/api/me/submissions/${encodeURIComponent(id)}${localeQuery(locale)}`,
  );
  return read(response, "This review was not found.");
}

export async function loadSubmission(token, locale) {
  const response = await fetch(
    `/api/submissions/${encodeURIComponent(token)}${localeQuery(locale)}`,
  );
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const missing = response.status === 404;
    throw new Error(missing ? "This review was not found." : payload.detail || "This review was not found.");
  }
  return payload;
}

const ADMIN_QUERY_LIMIT = 100;

function boundedAdminQuery({ q = "", cursor = "", limit = 25, ...filters } = {}) {
  const query = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value != null && String(value).trim()) {
      query.set(key, String(value).trim().slice(0, 100));
    }
  });
  const search = String(q).trim().slice(0, 200);
  const pageCursor = String(cursor).trim().slice(0, 1000);
  if (search) {
    query.set("q", search);
  }
  if (pageCursor) {
    query.set("cursor", pageCursor);
  }
  const requested = Number.parseInt(limit, 10);
  query.set("limit", String(Math.min(ADMIN_QUERY_LIMIT, Math.max(1, requested || 25))));
  return query.toString();
}

export async function loadAdminPipeline(options = {}) {
  const query = boundedAdminQuery(options);
  const response = await fetch(`/api/admin/pipeline?${query}`);
  return read(response, "Pipeline metrics are unavailable.");
}

export async function loadAdminFailures(options = {}) {
  const query = boundedAdminQuery(options);
  const response = await fetch(`/api/admin/failures?${query}`);
  return read(response, "Failures are unavailable.");
}

export async function loadAdminFailureSummary(options = {}) {
  const query = boundedAdminQuery(options);
  const response = await fetch(`/api/admin/failures/summary?${query}`);
  return read(response, "Failure summary is unavailable.");
}

export async function loadAdminOverview() {
  const response = await fetch("/api/admin/overview");
  return read(response, "Admin overview is unavailable.");
}

export async function loadAdminRequests(options = {}) {
  const query = boundedAdminQuery(options);
  const response = await fetch(`/api/admin/requests?${query}`);
  return read(response, "Requests are unavailable.");
}

export async function loadAdminRequest(id) {
  const response = await fetch(`/api/admin/requests/${encodeURIComponent(id)}`);
  return read(response, "This request was not found.");
}

export async function loadAdminUsers(options = {}) {
  const query = boundedAdminQuery(options);
  const response = await fetch(`/api/admin/users?${query}`);
  return read(response, "Users are unavailable.");
}

export async function loadAdminUser(id) {
  const response = await fetch(`/api/admin/users/${encodeURIComponent(id)}`);
  return read(response, "This user was not found.");
}
