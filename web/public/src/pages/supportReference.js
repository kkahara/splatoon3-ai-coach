const REFERENCE = /^(S3-)?[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{8}$/;

// Admin search input that names a support reference such as S3-K7M4P9Q2.
export function looksLikeReference(text) {
  return REFERENCE.test(String(text || "").replace(/\s+/g, "").toUpperCase());
}
