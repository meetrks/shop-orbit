"""
The only entry point the rest of the codebase (cart views, admin actions,
webhook view) is allowed to use to move money. Nothing outside this module
talks to `payments.gateways` directly.

Money never changes state based on what the browser reports — every path
that could mark a payment captured/failed either fetches the authoritative
state from the gateway itself (`verify_client_callback`) or comes from a
signature-verified webhook (`process_webhook`).
"""

import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .gateways import get_gateway
from .models import Payment, PaymentEvent, Refund, WebhookEvent

logger = logging.getLogger(__name__)

# Razorpay (and gateways generally) report a fresh refund as "processed"
# immediately for instant/UPI refunds, or "pending" for ones that settle
# later via a refund.processed/refund.failed webhook. Either way it's not
# yet a *failure*, so both map to Refund.Status.INITIATED until a webhook
# (or a later reconciliation fetch) reports a terminal outcome.
_REFUND_TERMINAL_STATUSES = {"processed": Refund.Status.PROCESSED, "failed": Refund.Status.FAILED}


class PaymentService:
    def __init__(self, gateway_name=None):
        self.gateway = get_gateway(gateway_name)

    # ------------------------------------------------------------------
    # Checkout: creating a payment attempt
    # ------------------------------------------------------------------

    def create_payment_for_order(self, order, user):
        """Creates a gateway order and a `Payment` row for a freshly-checked-out `Order`."""
        gateway_order = self.gateway.create_order(
            amount=order.total_amount,
            currency="INR",
            receipt=order.order_number,
            notes={"order_number": order.order_number, "user_id": str(user.id)},
        )
        payment = Payment.objects.create(
            order=order,
            user=user,
            gateway=self.gateway.name,
            gateway_order_id=gateway_order.gateway_order_id,
            amount=order.total_amount,
            currency=gateway_order.currency,
            status=Payment.Status.CREATED,
            raw_response=gateway_order.raw_response,
        )
        PaymentEvent.objects.create(
            payment=payment,
            event_type="created",
            new_status=payment.status,
            payload=gateway_order.raw_response,
        )
        return payment

    # ------------------------------------------------------------------
    # Client-side callback, right after Razorpay Checkout closes
    # ------------------------------------------------------------------

    def verify_client_callback(self, payment, *, gateway_payment_id, gateway_order_id, signature):
        """
        Verifies the signature Razorpay Checkout hands back to the
        browser, then fetches the payment's *actual* state from Razorpay
        directly rather than trusting anything the client claims about it.
        Returns True if the payment is now captured.

        This is an optimistic-UI fast path only — the webhook in
        `process_webhook` is the authoritative confirmation and will
        happily re-apply (idempotently) the same result if it arrives
        first, second, or is the only one of the two that ever arrives
        (e.g. the buyer closes the tab before this callback fires).
        """
        if gateway_order_id != payment.gateway_order_id:
            logger.warning(
                "Razorpay order id mismatch for payment %s: expected %s, got %s.",
                payment.pk,
                payment.gateway_order_id,
                gateway_order_id,
            )
            return False

        if not self.gateway.verify_payment_signature(
            gateway_order_id=gateway_order_id,
            gateway_payment_id=gateway_payment_id,
            signature=signature,
        ):
            # An unverified callback proves nothing either way — it could
            # be a genuinely failed payment, or it could be someone
            # POSTing garbage at this endpoint. Deliberately don't touch
            # `payment.status` here: only a signature-verified webhook or
            # an authoritative gateway fetch (below, once the signature
            # *does* check out) is allowed to move it. The buyer just sees
            # this attempt as unconfirmed and can retry; a real payment
            # failure still reaches FAILED via the payment.failed webhook.
            logger.warning("Signature verification failed for payment %s on client callback.", payment.pk)
            return False

        gateway_payment = self.gateway.fetch_payment(gateway_payment_id)
        self._apply_gateway_payment_state(payment, gateway_payment, source="client_verify", signature=signature)
        return gateway_payment.status == Payment.Status.CAPTURED

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def process_webhook(self, *, raw_body, headers):
        """
        Verifies and applies one webhook delivery. Returns True once the
        event has been durably recorded (whether or not it changed
        anything) — the view should respond 2xx in that case. Raises on
        genuine processing failure so the view can respond with an error
        status and let the gateway's own retry schedule redeliver it.
        """
        if not self.gateway.verify_webhook_signature(
            raw_body=raw_body, signature=headers.get("X-Razorpay-Signature", "")
        ):
            logger.warning("Rejected a webhook delivery with an invalid signature.")
            return False

        event = self.gateway.parse_webhook_event(raw_body=raw_body, headers=headers)

        webhook_event, created = WebhookEvent.objects.get_or_create(
            gateway=self.gateway.name,
            dedupe_key=event.dedupe_key,
            defaults={"event_type": event.event_type, "payload": event.payload},
        )
        if not created and webhook_event.processed_at is not None:
            # A true duplicate delivery of an event we already finished
            # processing — exactly what idempotency is for. No-op.
            return True

        try:
            self._dispatch_webhook_event(event)
        except Exception as exc:
            webhook_event.processing_error = str(exc)
            webhook_event.save(update_fields=["processing_error", "updated_at"])
            raise
        else:
            webhook_event.processed_at = timezone.now()
            webhook_event.processing_error = ""
            webhook_event.save(update_fields=["processed_at", "processing_error", "updated_at"])
        return True

    def _dispatch_webhook_event(self, event):
        if event.event_type in ("payment.authorized", "payment.captured", "order.paid"):
            self._handle_payment_state_webhook(event)
        elif event.event_type == "payment.failed":
            self._handle_payment_state_webhook(event)
        elif event.event_type == "refund.processed":
            self._handle_refund_webhook(event, status="processed")
        elif event.event_type == "refund.failed":
            self._handle_refund_webhook(event, status="failed")
        else:
            logger.info("Ignoring unhandled Razorpay webhook event type: %s", event.event_type)

    def _handle_payment_state_webhook(self, event):
        payment = self._find_payment(event.gateway_payment_id, event.gateway_order_id)
        if payment is None:
            logger.warning(
                "Webhook %s referenced an unknown payment/order (payment_id=%s, order_id=%s).",
                event.event_type,
                event.gateway_payment_id,
                event.gateway_order_id,
            )
            return
        gateway_payment = self.gateway.fetch_payment(event.gateway_payment_id)
        self._apply_gateway_payment_state(payment, gateway_payment, source=f"webhook:{event.event_type}")

    def _handle_refund_webhook(self, event, *, status):
        refund_entity = event.payload.get("payload", {}).get("refund", {}).get("entity", {})
        gateway_refund_id = refund_entity.get("id", "")
        refund = Refund.objects.filter(gateway_refund_id=gateway_refund_id).select_related("payment").first()
        if refund is None:
            logger.warning("Webhook %s referenced an unknown refund %s.", event.event_type, gateway_refund_id)
            return
        self._apply_refund_result(refund, gateway_status=status, raw_response=refund_entity)

    def _find_payment(self, gateway_payment_id, gateway_order_id):
        query = Q()
        if gateway_payment_id:
            query |= Q(gateway_payment_id=gateway_payment_id)
        if gateway_order_id:
            query |= Q(gateway_order_id=gateway_order_id)
        if not query:
            return None
        return Payment.objects.filter(query).select_for_update().first()

    # ------------------------------------------------------------------
    # Applying a gateway payment state (shared by client-verify + webhook)
    # ------------------------------------------------------------------

    def _apply_gateway_payment_state(self, payment, gateway_payment, *, source, signature=""):
        already_applied = (
            payment.status == gateway_payment.status
            and payment.gateway_payment_id == gateway_payment.gateway_payment_id
        )
        if already_applied:
            # Already applied — a duplicate webhook or a webhook arriving
            # after client-side verification already recorded the same
            # outcome. Nothing to do.
            return

        with transaction.atomic():
            previous_status = payment.status
            payment.gateway_payment_id = gateway_payment.gateway_payment_id
            payment.status = gateway_payment.status
            payment.method = gateway_payment.method
            payment.transaction_reference = gateway_payment.transaction_reference
            payment.failure_reason = gateway_payment.failure_reason
            payment.raw_response = gateway_payment.raw_response
            if signature:
                payment.signature = signature
            if gateway_payment.status == Payment.Status.CAPTURED and not payment.captured_at:
                payment.captured_at = timezone.now()
            payment.save()

            PaymentEvent.objects.create(
                payment=payment,
                event_type=source,
                previous_status=previous_status,
                new_status=payment.status,
                payload=gateway_payment.raw_response,
            )

            if payment.status == Payment.Status.CAPTURED and previous_status != Payment.Status.CAPTURED:
                from .pipeline import on_payment_captured

                transaction.on_commit(lambda: on_payment_captured(payment))
            elif payment.status == Payment.Status.FAILED and previous_status != Payment.Status.FAILED:
                from .pipeline import on_payment_failed

                transaction.on_commit(lambda: on_payment_failed(payment))

    def reconcile_payment(self, payment):
        """
        Staff-triggered reconciliation (admin action) for a payment stuck
        in CREATED/PENDING/AUTHORIZED — e.g. the buyer's browser closed
        before the client callback fired and the webhook was never
        delivered (network blip, misconfigured endpoint). Re-fetches the
        authoritative state directly from the gateway and applies it,
        exactly like a webhook would.
        """
        if not payment.gateway_payment_id:
            raise ValueError("This payment never reached the gateway — there's nothing to reconcile against.")
        gateway_payment = self.gateway.fetch_payment(payment.gateway_payment_id)
        self._apply_gateway_payment_state(payment, gateway_payment, source="admin_reconcile")
        return payment

    # ------------------------------------------------------------------
    # Refunds
    # ------------------------------------------------------------------

    def initiate_refund(self, payment, amount, *, reason="", initiated_by=None, return_request=None):
        """
        Initiates a full or partial refund against a captured payment.
        Staff-triggered, from the admin — either standalone or, when
        `return_request` is given, linked back to the return that
        prompted it (see `returns.services.initiate_return_refund`).
        """
        if payment.status not in (Payment.Status.CAPTURED, Payment.Status.PARTIALLY_REFUNDED):
            raise ValueError(f"Cannot refund a payment in status '{payment.status}'.")
        already_refunded = payment.refunded_amount
        if amount <= 0 or already_refunded + amount > payment.amount:
            raise ValueError("Refund amount must be positive and not exceed the remaining refundable balance.")

        gateway_refund = self.gateway.initiate_refund(payment.gateway_payment_id, amount, notes={"reason": reason})
        refund = Refund.objects.create(
            payment=payment,
            gateway_refund_id=gateway_refund.gateway_refund_id,
            amount=amount,
            status=Refund.Status.INITIATED,
            reason=reason,
            initiated_by=initiated_by,
            return_request=return_request,
            raw_response=gateway_refund.raw_response,
        )
        PaymentEvent.objects.create(
            payment=payment,
            event_type="refund_initiated",
            previous_status=payment.status,
            new_status=payment.status,
            payload=gateway_refund.raw_response,
        )

        if gateway_refund.status in _REFUND_TERMINAL_STATUSES:
            self._apply_refund_result(
                refund, gateway_status=gateway_refund.status, raw_response=gateway_refund.raw_response
            )
        return refund

    def _apply_refund_result(self, refund, *, gateway_status, raw_response):
        new_status = _REFUND_TERMINAL_STATUSES.get(gateway_status)
        if new_status is None or refund.status == new_status:
            return  # still pending, or already applied — nothing new to do.

        payment = refund.payment
        with transaction.atomic():
            refund.status = new_status
            refund.raw_response = raw_response or refund.raw_response
            refund.processed_at = timezone.now()
            if new_status == Refund.Status.FAILED:
                refund.failure_reason = (raw_response or {}).get("error_description", "")
            refund.save()

            if new_status == Refund.Status.PROCESSED:
                payment.refunded_amount = payment.refunded_amount + refund.amount
                payment.refund_status = (
                    Payment.RefundStatus.FULL
                    if payment.refunded_amount >= payment.amount
                    else Payment.RefundStatus.PARTIAL
                )
                payment.status = (
                    Payment.Status.REFUNDED
                    if payment.refund_status == Payment.RefundStatus.FULL
                    else Payment.Status.PARTIALLY_REFUNDED
                )
                payment.save()

            PaymentEvent.objects.create(
                payment=payment,
                event_type=f"refund_{new_status}",
                new_status=payment.status,
                payload=raw_response or {},
            )

            if new_status == Refund.Status.PROCESSED:
                from .pipeline import on_refund_processed

                transaction.on_commit(lambda: on_refund_processed(refund))
            else:
                from .pipeline import on_refund_failed

                transaction.on_commit(lambda: on_refund_failed(refund))
