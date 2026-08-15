"""
Global audit log registration (django-auditlog) — records who changed
what, when, and the field-level diff, across the business models below.
Kept centralized here rather than scattered across every app's models.py
since it's a cross-cutting concern; called once from `AccountsConfig
.ready()` (see accounts/apps.py), by which point every app's models are
already loaded regardless of INSTALLED_APPS order.

Deliberately NOT registered: models that are already their own audit
trail (`OrderStatusHistory`, `PaymentEvent`, `ShipmentStatusHistory`,
`StockMovement`, `WebhookEvent`, `CouponRedemption`) — auditing an audit
trail is redundant — and ephemeral pre-checkout state (`Cart`,
`CartItem`, `Wishlist`, `WishlistItem`) nobody needs a change history
for. `SupplierImageJob` (picweight) is an internal processing record,
not administrative data staff edit.

Every registration excludes `created_at`/`updated_at` — the log entry's
own `timestamp` already records *when*, so diffing a field that changes
on every single save would just add noise to every entry.
"""

from auditlog.registry import auditlog

_TIMESTAMPS = ["created_at", "updated_at"]


def register():
    from accounts.models import Address, User
    from cart.models import Coupon, Order, OrderItem
    from catalog.models import Category, Department, Product, ProductVariant, Review, StockAlert, Subcategory
    from fulfillment.models import Invoice, PackingSlip, PincodeServiceability, Shipment
    from inventory.models import PurchaseOrder, PurchaseOrderLine, Supplier
    from pages.models import (
        ContactMessage,
        HomeBanner,
        HomePriceTier,
        HomeSection,
        HomeSectionProduct,
        HomeTestimonialSection,
        HomeTestimonialSectionReview,
    )
    from payments.models import Payment, Refund
    from returns.models import ReturnRequest, ReturnRequestLine, ReturnShipment

    # Password hashes must never appear in a readable diff — masked, not
    # excluded outright, so a password *change* is still visible as an
    # event without leaking the value. last_login changes on every
    # login, same noise problem as updated_at.
    auditlog.register(User, exclude_fields=[*_TIMESTAMPS, "last_login"], mask_fields=["password"])
    auditlog.register(Address, exclude_fields=_TIMESTAMPS)

    auditlog.register(Department, exclude_fields=_TIMESTAMPS)
    auditlog.register(Category, exclude_fields=_TIMESTAMPS)
    auditlog.register(Subcategory, exclude_fields=_TIMESTAMPS)
    # search_vector is a derived field recomputed by catalog.signals on
    # every save — diffing it is meaningless noise, not an editorial change.
    auditlog.register(Product, exclude_fields=[*_TIMESTAMPS, "search_vector"])
    auditlog.register(ProductVariant, exclude_fields=_TIMESTAMPS)
    auditlog.register(Review, exclude_fields=_TIMESTAMPS)
    auditlog.register(StockAlert, exclude_fields=_TIMESTAMPS)

    auditlog.register(Coupon, exclude_fields=_TIMESTAMPS)
    auditlog.register(Order, exclude_fields=_TIMESTAMPS)
    auditlog.register(OrderItem, exclude_fields=_TIMESTAMPS)

    # raw_response is the full gateway payload (large, low signal) —
    # the normalized fields around it are what's worth diffing.
    auditlog.register(Payment, exclude_fields=[*_TIMESTAMPS, "raw_response"])
    auditlog.register(Refund, exclude_fields=[*_TIMESTAMPS, "raw_response"])

    auditlog.register(Invoice, exclude_fields=_TIMESTAMPS)
    auditlog.register(PackingSlip, exclude_fields=_TIMESTAMPS)
    auditlog.register(Shipment, exclude_fields=_TIMESTAMPS)
    auditlog.register(PincodeServiceability, exclude_fields=_TIMESTAMPS)

    auditlog.register(Supplier, exclude_fields=_TIMESTAMPS)
    auditlog.register(PurchaseOrder, exclude_fields=_TIMESTAMPS)
    auditlog.register(PurchaseOrderLine, exclude_fields=_TIMESTAMPS)

    auditlog.register(ReturnRequest, exclude_fields=_TIMESTAMPS)
    auditlog.register(ReturnRequestLine, exclude_fields=_TIMESTAMPS)
    auditlog.register(ReturnShipment, exclude_fields=_TIMESTAMPS)

    auditlog.register(ContactMessage, exclude_fields=_TIMESTAMPS)
    auditlog.register(HomeBanner, exclude_fields=_TIMESTAMPS)
    auditlog.register(HomeSection, exclude_fields=_TIMESTAMPS)
    auditlog.register(HomeSectionProduct, exclude_fields=_TIMESTAMPS)
    auditlog.register(HomeTestimonialSection, exclude_fields=_TIMESTAMPS)
    auditlog.register(HomeTestimonialSectionReview, exclude_fields=_TIMESTAMPS)
    auditlog.register(HomePriceTier, exclude_fields=_TIMESTAMPS)
