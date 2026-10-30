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

## Operator commands

All voucher commands read `PUBLIC_DATABASE_URL` and `PUBLIC_VOUCHER_HMAC_SECRET`
from the environment (the repo `.env` locally). Each environment has its own
secret: a local or test secret never hashes a production code, and the
production secret is only in `/srv/www/s3-coaching/.env` on the server. Never
commit it, paste it into documentation or chat, or pass it on a command line.

In production, run the commands inside the app container, which already has
the production environment:

```bash
ssh Hetzner
cd /srv/www/s3-coaching
docker compose exec app s3-coach public-admin voucher list
```

The examples below use fake values.

### Issue

`issue` (above) is the only command that ever shows a plaintext code, once,
at creation. The database keeps only the HMAC, so a lost code cannot be
recovered by `status --id` or `list`; issue a new one and revoke the old id.

### Status

```bash
s3-coach public-admin voucher status                # prompts; input is hidden
s3-coach public-admin voucher status SKT-AAAA-BBBB  # code as an argument
s3-coach public-admin voucher status --id 00000000-0000-4000-8000-000000000000
```

Use the prompt form for a real code. A code given as an argument can stay in
shell history. Code lookups use the same normalization and HMAC as the site
and need the secret; `--id` does not. A code and `--id` together are refused.
An unknown code or id prints `voucher was not found` and exits non-zero.

Status is read-only. It never validates through the public endpoint, attaches,
or redeems. It prints:

```
voucher_id=00000000-0000-4000-8000-000000000000 state=fully_used
source=skilltown campaign=skilltown_example
service=match_analysis benefit=externally_prepaid
uses=1/1 expires_at=2026-12-31T23:59:59+00:00
redeemed_at=2026-11-02T10:15:00+00:00
```

`state` uses the redemption rules: `active` (a redemption now would pass),
`fully_used`, `expired`, `revoked`, `inactive` (before `starts_at`), or
`wrong_service`. `uses` counts committed `voucher_redemptions` rows; there is
one `redeemed_at` line per row. `starts_at` and the operator note appear when
set. Status never prints the code, its HMAC, submission ids, or customer data.

### List

```bash
s3-coach public-admin voucher list
```

```
CREATED            CAMPAIGN           SOURCE     SERVICE         STATE       USED  LIMIT  EXPIRES            ID
2026-11-01T09:00Z  skilltown_example  skilltown  match_analysis  active      0     1      2026-12-31T23:59Z  00000000-0000-4000-8000-000000000001
2026-10-20T12:30Z  youtube_example    youtube    match_analysis  fully_used  20    20     2026-12-31T23:59Z  00000000-0000-4000-8000-000000000002
```

Every voucher, newest first, times in UTC. Read-only, and it does not need the
HMAC secret. It shows no codes, HMACs, notes, submission ids, or customer data.
Use the id with `status --id` or `revoke`.

### Revoke

Revoke stops a later redemption. It does not cancel a job that already queued.

```bash
s3-coach public-admin voucher revoke 00000000-0000-4000-8000-000000000000
```

## Not production-ready from code review alone

Do not deploy this, issue real customer codes, or change production data until
the manual checks in the implementation report have been run.
