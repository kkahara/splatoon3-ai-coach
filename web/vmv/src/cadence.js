/** Cadence tiles and timeline placement. Display only; no event inference. */

export const CHIP_DETECTORS = [
  "timer",
  "death",
  "splat",
  "respawn",
  "active_gameplay",
  "map_overlay",
  "special_gauge",
];

export function groupTiles(observations) {
  const map = new Map();
  for (const obs of observations) {
    const key = obs.frame_id || `ts:${obs.timestamp}`;
    let tile = map.get(key);
    if (!tile) {
      tile = {
        key,
        timestamp: obs.timestamp,
        image_relpath: obs.image_relpath || null,
        readings: [],
      };
      map.set(key, tile);
    }
    if (!tile.image_relpath && obs.image_relpath) tile.image_relpath = obs.image_relpath;
    if (obs.detector) tile.readings.push(obs);
  }
  return [...map.values()].sort(
    (a, b) => a.timestamp - b.timestamp || String(a.key).localeCompare(String(b.key)),
  );
}

export function tilePasses(tile, detector, confidence) {
  if (detector) {
    const reading = tile.readings.find((item) => item.detector === detector);
    if (!reading || !reading.positive) return false;
    if (confidence > 0 && (reading.confidence == null || reading.confidence < confidence)) {
      return false;
    }
    return true;
  }
  if (confidence > 0) {
    return tile.readings.some(
      (item) => item.positive && item.confidence != null && item.confidence >= confidence,
    );
  }
  return true;
}

export function preferredReading(tile, detector) {
  if (!tile?.readings.length) return null;
  if (detector) {
    const hit = tile.readings.find((item) => item.detector === detector);
    if (hit) return hit;
  }
  const rank = (item) => {
    const positive = item.positive ? 200 : 0;
    const timerBias = item.detector === "timer" ? 0 : 50;
    return positive + timerBias + (item.confidence || 0);
  };
  return tile.readings.slice().sort((a, b) => rank(b) - rank(a))[0];
}

export function nearestTile(tiles, time) {
  if (!tiles.length) return null;
  return tiles.reduce((best, tile) =>
    Math.abs(tile.timestamp - time) < Math.abs(best.timestamp - time) ? tile : best,
  );
}

export function placeMarks(marks, duration, width) {
  const minGapPx = 96;
  const laneTops = [];
  const placed = marks
    .slice()
    .sort((a, b) => a.timestamp - b.timestamp)
    .map((mark) => {
      const leftPct = (mark.timestamp / duration) * 100;
      const x = (leftPct / 100) * width;
      let lane = 0;
      while (lane < laneTops.length && x - laneTops[lane] < minGapPx) lane += 1;
      if (lane === laneTops.length) laneTops.push(-1e9);
      laneTops[lane] = x;
      return { mark, leftPct, lane };
    });
  const maxLane = placed.length ? Math.max(...placed.map((item) => item.lane)) : 0;
  return { placed, rowHeight: (maxLane + 1) * 46 };
}

export function chipText(reading) {
  const name = (reading.detector || "").toUpperCase();
  if (!reading.positive) return `${name} −`;
  const score = reading.confidence != null ? reading.confidence.toFixed(2) : "—";
  return `${name} ${score}+`;
}
