/** JSON helpers. The UI renders API records and does not infer match events. */

export async function api(path, options = {}) {
  const headers = options.body instanceof FormData ? undefined : { "Content-Type": "application/json" };
  const response = await fetch(path, { ...options, headers });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = data && data.detail ? data.detail : response.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}
