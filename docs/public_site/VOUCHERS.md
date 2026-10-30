# Vouchers (V1)

Vouchers authorize the existing match-analysis service without Stripe. They are
not discounts. The only service id is `match_analysis`.

A code is not redeemed when someone types it or presses Apply.
`POST /api/vouchers/validate` only checks it. The submit request may attach
the voucher to a new submission (`voucher_state=pending`) while
`payment_state` stays `unpaid`. That attachment does not satisfy `_ready` or
the queue update.

The voucher is redeemed only after the upload probe succeeds, in one Postgres
transaction that:

1. locks the submission gate, then the voucher;
2. revalidates the voucher;
3. inserts one `voucher_redemptions` row;
4. sets `voucher_state=redeemed` and `payment_state=not_required`;
5. applies the existing queue predicate.

Until that transaction commits, the submission is not authorized. A pending
voucher does not make `payment_required` false. The submit page uses
`voucher_state=pending` only to avoid opening Stripe before the probe.

If the voucher has expired, been revoked, or reached `max_redemptions` by the
time of that transaction, no redemption is inserted, the gate stays `unpaid`,
`voucher_state` becomes `failed`, and the review page can continue with the
existing Stripe checkout. Checkout is refused only while the voucher is still
`pending`, so a failed voucher does not block payment. Checkout and redemption
both lock the gate; a Checkout attempt cannot start while redemption can, and
redemption refuses a gate that already has a Checkout attempt or session.

Voucher authorization does not bypass file checks, the video probe, upload
limits, or the submission rate limit. Validate requests have their own limit
of 20 per hour per network.

## Issuing

Requires `PUBLIC_DATABASE_URL` and `PUBLIC_VOUCHER_HMAC_SECRET`. The secret is
only in the environment. If it is missing, voucher requests fail and submissions
without a code behave as they do today. Do not commit the secret.

Codes are normalized before hashing: NFKC, strip surrounding whitespace, and
Unicode uppercase. Internal whitespace is rejected. Hyphens stay.
`squidlab50` and `SKT-7k4m-p9qx` match the issued forms. The database stores
HMAC-SHA256 of that form, never the plaintext. Request logs must not contain it.
The issue command prints the code once.

Expiration uses server UTC time. A code is valid while `now < expires_at` and,
when set, `now >= starts_at`. `max_redemptions` counts committed redemption
rows. Validity ends at the earlier of expiration and that count. A retry of a
submission that is already redeemed, `not_required`, and queued does not add
another row. Any other existing authorization state fails without being changed.

Postgres commits the redemption and the queued gate together. The following
disk write that marks the submission queued is separate, as it is after Stripe
payment today. If that disk write fails, the voucher is already redeemed and
the gate is already queued; this is not an end-to-end exactly-once claim.
Posting the upload confirmation again retries the disk write when the file
record is still unqueued. There is no new recovery worker.

### Skilltown

One generated code per purchase. Normally one use, benefit
`externally_prepaid`.

```bash
s3-coach public-admin voucher issue \
  --source skilltown \
  --campaign skilltown_2026 \
  --expires-at 2026-12-31T23:59:59+00:00
```

The printed code looks like `SKT-7K4M-P9QX`. Send that line to the customer.
It is not saved.

### YouTube

The public code is the one you choose. It is meant to be shared. Benefit
`complimentary`. `--max-redemptions` is required.

```bash
s3-coach public-admin voucher issue \
  --source youtube \
  --campaign youtube_outreach_2026 \
  --code SQUIDLAB50 \
  --max-redemptions 20 \
  --expires-at 2026-12-31T23:59:59+00:00
```

`youtube_outreach_2026` is the campaign, not the code viewers type.

## Revoke and inspect

Revoke stops a later redemption. It does not cancel a job that already queued.

```bash
s3-coach public-admin voucher revoke 00000000-0000-4000-8000-000000000000
s3-coach public-admin voucher status 00000000-0000-4000-8000-000000000000
```

Status prints the campaign, expiry, redemption count, and submission ids.
It cannot print the code.

## Not production-ready from code review alone

Do not deploy this, issue real customer codes, or change production data until
the manual checks in the implementation report have been run.
