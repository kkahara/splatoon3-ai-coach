/** Display names for ground-truth labels already defined by the viewer. */

export const GT_LABELS = {
  unknown: "Unknown",
  real_death: "Real death",
  not_a_death: "Not a death",
  damage_recovery: "Damage/recovery",
  results_screen: "Results screen",
  lobby: "Lobby",
  real_splat: "Real splat",
  not_a_splat: "Not a splat",
  real_active_gameplay: "Real active gameplay",
  not_active_gameplay: "Not active gameplay",
  real_map_overlay: "Real map overlay",
  not_a_map_overlay: "Not an overlay",
  real_respawn: "Real respawn",
  not_a_respawn: "Not a respawn",
  real_timer: "Real timer",
  not_a_timer: "Not a timer",
  special_used: "SPECIAL_USED",
  not_a_special_used: "NOT_SPECIAL_USED",
  uncertain: "UNCERTAIN",
  intro: "Intro",
  opening_countdown: "Opening countdown",
  in_match: "In match",
  post_match: "Post match",
  results_lobby: "Results / lobby",
};

export const GT_BY_DETECTOR = {
  death: ["unknown", "real_death", "not_a_death", "damage_recovery"],
  splat: ["unknown", "real_splat", "not_a_splat"],
  active_gameplay: ["unknown", "real_active_gameplay", "not_active_gameplay"],
  map_overlay: ["unknown", "real_map_overlay", "not_a_map_overlay"],
  respawn: ["unknown", "real_respawn", "not_a_respawn"],
  timer: ["unknown", "real_timer", "not_a_timer"],
  special_gauge: ["unknown", "special_used", "not_a_special_used", "uncertain"],
  match_phase: ["unknown", "intro", "opening_countdown", "in_match", "post_match", "results_lobby"],
};

export const GT_MEANINGS = {
  special_used:
    "A special was actually consumed at this point. A fill drop is not consumption.",
  not_a_special_used:
    "No special was consumed. Death charge loss, flicker, or a fill re-estimate.",
  uncertain: "The available evidence does not show whether a special was consumed.",
  real_death: "DEATH event: first alive to dead transition. Later Ouch frames stay unmarked.",
  not_a_death: "Death detector false positive.",
};

export function labelsFor(channel, detector) {
  if (channel === "special_usage") return GT_BY_DETECTOR.special_gauge;
  if (channel === "match_phase") return GT_BY_DETECTOR.match_phase;
  return GT_BY_DETECTOR[detector] || ["unknown"];
}

export function detectorForChannel(channel, detector) {
  if (channel === "special_usage") return "special_gauge";
  if (channel === "match_phase") return "match_phase";
  return detector || "death";
}

export function formatTime(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return "—";
  const sign = seconds < 0 ? "-" : "";
  const abs = Math.abs(seconds);
  const mins = Math.floor(abs / 60);
  const secs = abs - mins * 60;
  return `${sign}${mins}:${secs.toFixed(1).padStart(4, "0")}`;
}
