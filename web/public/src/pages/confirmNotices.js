/** Relative review paths only; never accept an absolute or off-site URL. */
export function safeReviewPath(path) {
  if (typeof path !== "string") {
    return null;
  }
  if (!path.startsWith("/review/") || path.includes("://") || path.includes("..")) {
    return null;
  }
  const token = path.slice("/review/".length);
  if (!token || token.includes("/") || token.includes("?")) {
    return null;
  }
  return path;
}
