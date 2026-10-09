export function AdminError({ message }) {
  return message ? <p className="error">{message}</p> : null;
}

export function AdminLoading() {
  return <p>Loading…</p>;
}

export function displayLabel(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function formatDate(value) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("en-US");
}

export function formatDuration(value) {
  if (value == null || value === "") {
    return "—";
  }
  const seconds = Number(value);
  if (!Number.isFinite(seconds)) {
    return String(value);
  }
  if (seconds < 60) {
    return `${seconds.toFixed(seconds % 1 ? 1 : 0)}s`;
  }
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds % 60)}s`;
}

export function formatValue(value) {
  if (value == null || value === "") {
    return "—";
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (Array.isArray(value)) {
    return value.length ? value.map(formatValue).join(", ") : "—";
  }
  if (typeof value === "object") {
    return Object.entries(value)
      .map(([key, item]) => `${displayLabel(key)}: ${formatValue(item)}`)
      .join(" · ");
  }
  return String(value);
}

export function listPayload(payload, keys) {
  if (Array.isArray(payload)) {
    return { items: payload, nextCursor: null };
  }
  const items = keys.map((key) => payload?.[key]).find(Array.isArray) || [];
  return {
    items,
    nextCursor: payload?.next_cursor ?? payload?.nextCursor ?? null,
  };
}

export function DetailList({ rows }) {
  return (
    <dl className="admin-details">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{formatValue(value)}</dd>
        </div>
      ))}
    </dl>
  );
}
