/** Clock time when this browser may ask for another confirmation email, or 0. */
export function cooldownUntil(seconds, now = Date.now()) {
  const value = Number(seconds);
  return Number.isFinite(value) && value > 0 ? now + value * 1000 : 0;
}

/** Whole minutes left, rounded up; 0 once the server would accept a resend. */
export function cooldownMinutes(until, now = Date.now()) {
  return until > now ? Math.ceil((until - now) / 60000) : 0;
}
