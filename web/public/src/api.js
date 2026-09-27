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

export async function loadSubmission(token) {
  const response = await fetch(`/api/submissions/${encodeURIComponent(token)}`);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const missing = response.status === 404;
    throw new Error(missing ? "This review was not found." : payload.detail || "This review was not found.");
  }
  return payload;
}
