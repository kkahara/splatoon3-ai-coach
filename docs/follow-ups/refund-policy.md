# Refund policy and Stripe follow-up

The submit page no longer says an administrator can refund a charge. No refund capability was added.

## What the code does today

Checkout is one Stripe `mode="payment"` session per submission. This site stores the Checkout session id, Checkout URL, payment-intent id, snapshotted price, currency, and payment state. Card numbers are entered on Stripe's page and are not fields in this app.

There is no refund API call, no admin refund action, and no automatic refund for an internal failure or an invalid upload. A person with access to the Stripe dashboard could still refund a charge outside this app. The software neither does that nor records it.

## Decision needed

Choose one policy and, if refunds should happen inside the product, implement it before describing it:

- Charges are not refunded through this site. Say only that, and keep any Stripe-dashboard refund as an operational choice that the site does not advertise.
- An administrator can refund defined cases. That needs a real Stripe refund action, a record of the refund, and rules for which failures qualify.

Until that exists, public copy should not say that an administrator can refund a charge.
