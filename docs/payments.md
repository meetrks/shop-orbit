# Payments Architecture

shoporbit charges customers through **Razorpay**, reached exclusively
through a gateway-agnostic payment layer in the `payments` app. This
document covers the full checkout flow, the webhook/idempotency design,
refunds, and how to add a second gateway later.

## Apps involved

| App | Responsibility |
|---|---|
| `cart` | Cart, `Order`/`OrderItem`, checkout UI. Never talks to Razorpay directly. |
| `payments` | The only app that talks to a payment gateway. `Payment`/`Refund`/`WebhookEvent` models, the gateway abstraction, `PaymentService`, the webhook receiver. |
| `fulfillment` | GST `Invoice`, `PackingSlip`, `Shipment` — generated once payment is captured. |
| `catalog` | `Product.hsn_code` / `Product.gst_rate` feed GST calculation; `Product.stock_count` is what gets reduced on capture. |

## Gateway abstraction

```
payments/gateways/base.py       PaymentGateway (ABC) + normalized dataclasses
payments/gateways/razorpay_gateway.py   RazorpayGateway(PaymentGateway)
payments/gateways/__init__.py   get_gateway(name) -> cached instance, via GATEWAY_CLASSES
```

Nothing outside `payments.gateways.razorpay_gateway` imports the
`razorpay` SDK or knows about paise, Razorpay's status vocabulary, or its
webhook envelope shape. Every gateway method returns one of four
normalized dataclasses (`GatewayOrder`, `GatewayPayment`, `GatewayRefund`,
`WebhookEventData`) so `payments.services.PaymentService` never branches
on a provider-specific string.

**Adding a second gateway** (Cashfree, PhonePe, PayU, ...): write a new
`PaymentGateway` subclass, add it to `GATEWAY_CLASSES` in
`payments/gateways/__init__.py`, and set `DEFAULT_PAYMENT_GATEWAY` (or
pass `gateway_name=` to `PaymentService(...)`). Nothing in `cart`,
`fulfillment`, or the admin changes.

## The `Payment` model and its states

`payments.models.Payment.Status`: `created` → `pending`/`authorized` →
`captured` → (optionally) `refunded`/`partially_refunded`, or `failed`/
`cancelled`. Every transition is logged to `PaymentEvent` (append-only
audit trail: who/what caused it, previous → new status, the raw gateway
payload). `Refund` records one full/partial refund attempt against a
captured payment; a `Payment` can have many `Refund`s.

`Order.status` (in `cart`) is the fulfillment status, not the payment
status — it only reaches `PAYMENT_CONFIRMED` once a `Payment` under it is
`captured`. See the state mapping in `payments/pipeline.py`.

## Checkout flow

```
Buyer submits checkout form (cart.views.checkout)
  -> validate stock, coupon, pricing (server-side, cart already computed subtotal/discount)
  -> create Order (status=AWAITING_PAYMENT) + OrderItems (frozen price/tax snapshot)
  -> redirect to cart.views.checkout_payment

checkout_payment (GET)
  -> PaymentService.create_payment_for_order(order, user)
       -> gateway.create_order(...) [Razorpay API call]
       -> Payment row created (status=CREATED)
  -> render Razorpay Checkout.js modal (checkout_payment.html), auto-opened

Buyer completes payment in the Razorpay modal
  -> Checkout.js "handler" callback fires client-side with
     {razorpay_payment_id, razorpay_order_id, razorpay_signature}
  -> JS POSTs this to payments:verify_payment

payments.views.verify_payment (POST, login required)
  -> PaymentService.verify_client_callback(...)
       -> gateway.verify_payment_signature(...)   [reject if invalid; payment.status left untouched]
       -> gateway.fetch_payment(payment_id)       [never trust the client's own claim]
       -> _apply_gateway_payment_state(...)       [updates Payment, logs PaymentEvent]
       -> if now CAPTURED: payments.pipeline.on_payment_captured (via transaction.on_commit)
  -> JSON {success, redirect_url} -> browser redirects to success/failure page

Razorpay also POSTs a webhook (asynchronously, independent of the above)
  -> payments.views.razorpay_webhook (POST, csrf_exempt, no login — signature IS the auth)
  -> PaymentService.process_webhook(...)
       -> verify webhook signature (HMAC, RAZORPAY_WEBHOOK_SECRET)
       -> dedupe via WebhookEvent (see Idempotency below)
       -> re-fetch authoritative payment state from Razorpay (never trust the webhook payload's amount/status blindly beyond routing)
       -> same _apply_gateway_payment_state(...) as above
```

The client-side verification is an **optimistic-UI fast path only** — it
gives the buyer an instant redirect instead of waiting on the webhook. The
webhook is the **authoritative** confirmation and is designed to arrive
before, after, or as the *only* one of the two that ever fires (e.g. the
buyer's tab closes before the callback runs). Both paths funnel through
the same `_apply_gateway_payment_state`, which is idempotent by
comparing the incoming status against what's already stored.

## What happens on capture (`payments/pipeline.py`)

`on_payment_captured(payment)`, guarded by `Order.status ==
AWAITING_PAYMENT` (so a second call — webhook after client-verify, or
vice versa — is a no-op):

1. Re-validate and decrement stock (`select_for_update` per product/variant — a second, DB-locked check, since stock was only advisory-checked at checkout time and could have changed in the gap).
2. `Order.status = PAYMENT_CONFIRMED`.
3. `fulfillment.services.generate_invoice(order)` — GST invoice, numbered `{PREFIX}/{financial_year}/{seq:06d}`, CGST+SGST if `order.shipping_state == COMPANY_STATE`, else IGST. Tax is backed out of the tax-inclusive `OrderItem.unit_price` using each item's frozen `hsn_code`/`gst_rate`.
4. `fulfillment.services.generate_packing_slip(order)` — PDF with a Code128 barcode of the order number.
5. `fulfillment.services.create_shipment(order)` — a `Shipment` row in `PENDING` status; staff assign a carrier and tracking link from the admin once it's actually picked up.
6. `payments.emails.send_payment_succeeded_email` (buyer, with the invoice link) and `cart.emails.send_new_order_staff_notification` (the operator, via `STAFF_NOTIFICATION_EMAIL`).

`on_payment_failed` / `on_refund_processed` / `on_refund_failed` follow the
same idempotent-guard pattern and send their own dedicated email.
Order-status transitions triggered from this module set
`order._skip_status_email = True` before saving, so `cart.signals`' generic
"your order status changed" email (used for staff-driven transitions like
marking a shipment delivered) doesn't also fire — these already have a
richer, purpose-built email.

## Idempotency

Two independent mechanisms, for two independent race conditions:

- **Duplicate webhook deliveries** (`payments.models.WebhookEvent`): every
  verified webhook is recorded by `(gateway, dedupe_key)` — Razorpay's own
  `X-Razorpay-Event-Id` header when present, otherwise a hash of the event
  payload. A delivery whose `(gateway, dedupe_key)` already has
  `processed_at` set is a no-op; a previously-*failed* delivery (exception
  raised mid-processing) is retried, since `processed_at` was never set.
- **Racing client-verify vs. webhook** (`Payment.status` comparison in
  `_apply_gateway_payment_state`): both paths re-fetch the authoritative
  state from Razorpay and only act if it actually changed anything, so
  whichever arrives first runs the pipeline and the second is a no-op.
- **Order-level guard** (`Order.status == AWAITING_PAYMENT` check in
  `payments.pipeline`): belt-and-braces in case a payment somehow got
  applied twice — stock is never decremented twice for the same order.

## Refunds

Staff-initiated from the Payment admin (`payments/admin.py`): "Initiate
refund" opens a small form (amount, reason), which calls
`PaymentService.initiate_refund(payment, amount, reason=..., initiated_by=request.user)`.
This calls `gateway.initiate_refund(...)` and creates a `Refund` row.
Razorpay reports instant refunds (e.g. UPI) as immediately `processed`;
others stay `pending` until a `refund.processed`/`refund.failed` webhook
confirms the outcome. Either way, the same `_apply_refund_result` is used,
so a refund initiated from the admin and one confirmed asynchronously via
webhook can't double-apply.

A full refund sets `Order.status = REFUNDED`; a partial one sets
`PARTIALLY_REFUNDED`. `Payment.refunded_amount` accumulates across
multiple partial refunds; `Payment.refund_status` (`none`/`partial`/`full`)
is derived from it.

## Security

- **Signatures, always server-side.** `verify_payment_signature` (client
  callback) and `verify_webhook_signature` (webhook) both call into the
  Razorpay SDK's own HMAC verification — never a hand-rolled comparison.
- **Client callback never sets FAILED.** A bad signature on
  `verify_client_callback` just returns `False` and leaves `Payment.status`
  untouched — it proves nothing (could be a real failure, could be
  someone probing the endpoint with garbage). Only a signature-verified
  webhook, or a signature-verified *and gateway-confirmed* client callback,
  can move status forward.
- **CSRF exemption on the webhook is intentional and safe**: it's a
  server-to-server POST with no session/cookie, so a CSRF token isn't
  meaningful there — the HMAC signature is what stands in for auth.
- **Amounts are always re-fetched from the gateway**, never taken from
  request data. `verify_client_callback` and every webhook handler call
  `gateway.fetch_payment(...)` rather than trusting the payload.

## Admin

- **Payments** (`payments.Payment`): filter by status/gateway/method,
  search by order number / gateway IDs / transaction reference, "Initiate
  refund" per payment, "Reconcile with gateway" bulk action (re-fetches
  from Razorpay for payments stuck in a non-terminal state — e.g. the
  webhook was never delivered), "Export as CSV".
- **Refunds** (`payments.Refund`), **Webhook events**
  (`payments.WebhookEvent`, for debugging delivery/processing issues) are
  read-only.
- **Invoices / Packing slips / Shipments** (`fulfillment.*`): read-only
  except `Shipment`, where staff set `carrier`, `tracking_number`,
  `tracking_url` (pasted directly from the courier — not templated from a
  carrier name, since tracking URL formats vary and aren't something this
  codebase should guess at), and `status`. Marking a shipment
  picked-up/in-transit/delivered automatically advances `Order.status`
  (see `fulfillment/signals.py`) and fires the buyer's status-change email.

## Environment variables

See `.env.example`. At minimum: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`
(from the Razorpay dashboard), `RAZORPAY_WEBHOOK_SECRET` (set when you
create the webhook — see below). `DEFAULT_PAYMENT_GATEWAY` and
`INVOICE_SERIES_PREFIX` have sensible defaults.

## Setting up the webhook in the Razorpay dashboard

1. Settings → Webhooks → add a webhook pointed at
   `https://yourdomain/payments/razorpay/webhook/`.
2. Subscribe to at least: `payment.authorized`, `payment.captured`,
   `payment.failed`, `refund.processed`, `refund.failed`, `order.paid`.
3. Copy the webhook secret Razorpay generates into `RAZORPAY_WEBHOOK_SECRET`
   — this is **not** your API key secret.

## Testing

`payments/tests/fakes.py` provides `FakeGateway`, a scriptable in-memory
stand-in for `PaymentGateway` used by every test in `payments`, `cart`,
and (indirectly) `fulfillment` — none of the test suite makes network
calls to Razorpay. Patch `payments.services.get_gateway` to inject it:

```python
patcher = mock.patch("payments.services.get_gateway", return_value=FakeGateway())
```

`payments/tests/test_gateways.py` covers `RazorpayGateway`'s own
wire-format handling (paise conversion, status/method mapping, webhook
parsing) against literal Razorpay-shaped payloads, without the fake.
