export const MIN_COMPARISON_N = 10;

export function sampleNote(n) {
  const count = Number(n);
  if (!Number.isFinite(count)) {
    return "Sample size unavailable";
  }
  if (count < MIN_COMPARISON_N) {
    return `n=${count} — insufficient sample for stable comparison`;
  }
  return `n=${count}`;
}

export function formatAverage(value) {
  if (value == null || value === "") {
    return "—";
  }
  const amount = Number(value);
  if (!Number.isFinite(amount)) {
    return "—";
  }
  return amount.toFixed(2);
}

export function formatEstimatedCost(value) {
  if (value == null || value === "") {
    return "—";
  }
  const amount = Number(value);
  if (!Number.isFinite(amount)) {
    return "—";
  }
  return `$${amount.toFixed(4)}`;
}

export function formatRate(value) {
  if (value == null || value === "") {
    return "—";
  }
  const rate = Number(value);
  if (!Number.isFinite(rate)) {
    return "—";
  }
  return `${Math.round(rate * 1000) / 10}%`;
}
