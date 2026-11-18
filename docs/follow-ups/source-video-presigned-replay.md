# Source video: presigned-URL replay after deletion (deferred)

Status: **deferred technical debt, frozen.** This is a potential issue, not a
confirmed incident. Do not change deletion, expiry, the sweep or bucket
lifecycle rules for it until the owner reopens the topic.

## Facts

- Each uploaded-video submission gets one presigned PUT URL when it is
  created (`SubmissionService` → `R2Storage.presign_put`). The URL is for the
  key `submissions/<submission_id>/source<suffix>` and signs host,
  `Content-Type` and `Content-Length`, with an unsigned payload. It is valid
  for `PublicSettings.upload_url_seconds` (900 seconds, not set from the
  environment) and can be used more than once within that time. R2 cannot revoke one URL on its own.
- After a review is stored, `release_source_video` sends a DELETE, then a HEAD,
  and then clears `VideoInput.object_key`.
- If the original URL is replayed before it expires, it can recreate an object
  at that key. The application no longer tracks the key, so nothing would
  delete the recreated object.
- The other source-delete paths (`_fail`, the size-cap delete in
  `_require_owned`, `_expire_one`, `_delete_unpaid`) either keep `object_key` or
  run long after the URL has expired.

## Evidence (dev bucket, synthetic objects only)

E4 ran on `s3-coaching-dev` on 2026-11-10 with synthetic `e4e4e4e4…`
submission IDs. Every recorded key was deleted, and the bucket listing was the
same before and after. Results are in `/private/tmp/s3e4/` (not committed).

- A replay after DELETE and before expiry returned 200. The object was
  recreated with different bytes of the same size.
- A replay after expiry returned 403 `ExpiredRequest`.
- A PUT that started before expiry finished after it and was stored (200).
  That held for transfers that ended 59 s and about 455 s after expiry.
- R2 ended uploads that sent nothing for roughly 10–15 s (connection reset,
  500 `InternalError` or a broken pipe), and stored nothing for them.

## Options recorded, not chosen

- Keep the key, and its deletion state, until the URL can no longer create the
  object. Then delete again and verify.
- Add an R2 lifecycle rule on `submissions/` as a backstop. Its age must be at
  least the longest legitimate source lifetime, which is 365 days for account
  submissions.
- Shorten the URL lifetime. This narrows the window but does not close it.
