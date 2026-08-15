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
"""

from auditlog.registry import auditlog

_TIMESTAMPS = ["created_at", "updated_at"]


def _reverse_relation_names(model):
    """
    django-auditlog's own field selection (auditlog.diff.get_fields_in_model)
    only skips many-to-many fields — it does NOT skip reverse relations
    (the "other model points at this one" side, e.g. Product.reviews,
    Product.order_items). Diffing those produces garbage on create/delete
    ("reviews": ["catalog.Review.None", "None"]) since a reverse manager
    isn't a real field value. `field.concrete` is False for exactly these
    (and for GenericRelations), so excluding non-concrete fields removes
    the noise without having to hand-list every reverse accessor per model.
    """
    return [f.name for f in model._meta.get_fields() if not getattr(f, "concrete", True)]


def _register(model, *, exclude=(), **kwargs):
    auditlog.register(model, exclude_fields=[*_TIMESTAMPS, *_reverse_relation_names(model), *exclude], **kwargs)


def register():
    from accounts.models import Address, User
    from cart.models import Coupon, Order, OrderItem
    from catalog.models import Category, Department, Product, ProductVariant, Review, StockAlert, Subcategory
    from fulfillment.models import Invoice, PackingSlip, PincodeServiceability, Shipment
    from inventory.models import PurchaseOrder, PurchaseOrderLine, Supplier
    from pages.models import (
        ContactMessage,
        HomeBanner,
        HomeCategorySpotlightSection,
        HomeCategorySpotlightTile,
        HomeGalleryItem,
        HomeGallerySection,
        HomeLifestyleSection,
        HomeLifestyleTile,
        HomeLovedByQuote,
        HomeLovedBySection,
        HomePriceTier,
        HomePromoBanner,
        HomeSection,
        HomeSectionProduct,
        HomeTestimonialSection,
        HomeTestimonialSectionReview,
        HomeTrustStripItem,
        HomeTrustStripSection,
        HomeValuePropItem,
        HomeValuePropSection,
    )
    from payments.models import Payment, Refund
    from returns.models import ReturnRequest, ReturnRequestLine, ReturnShipment

    # Password hashes must never appear in a readable diff — masked, not
    # excluded outright, so a password *change* is still visible as an
    # event without leaking the value. last_login changes on every
    # login, same noise problem as updated_at.
    _register(User, exclude=["last_login"], mask_fields=["password"])
    _register(Address)

    _register(Department)
    _register(Category)
    _register(Subcategory)
    # search_vector is a derived field recomputed by catalog.signals on
    # every save — diffing it is meaningless noise, not an editorial
    # change. eav_values is a GenericRelation added dynamically by
    # catalog.apps.CatalogConfig.ready()'s eav.register(Product) call,
    # which runs after this one (accounts loads before catalog in
    # INSTALLED_APPS) — _reverse_relation_names() can't see it yet, so
    # it's excluded explicitly rather than depending on ready() order.
    _register(Product, exclude=["search_vector", "eav_values"])
    _register(ProductVariant)
    _register(Review)
    _register(StockAlert)

    _register(Coupon)
    _register(Order)
    _register(OrderItem)

    # raw_response is the full gateway payload (large, low signal) —
    # the normalized fields around it are what's worth diffing.
    _register(Payment, exclude=["raw_response"])
    _register(Refund, exclude=["raw_response"])

    _register(Invoice)
    _register(PackingSlip)
    _register(Shipment)
    _register(PincodeServiceability)

    _register(Supplier)
    _register(PurchaseOrder)
    _register(PurchaseOrderLine)

    _register(ReturnRequest)
    _register(ReturnRequestLine)
    _register(ReturnShipment)

    _register(ContactMessage)
    _register(HomeBanner)
    _register(HomeSection)
    _register(HomeSectionProduct)
    _register(HomeTestimonialSection)
    _register(HomeTestimonialSectionReview)
    _register(HomePriceTier)
    _register(HomeLifestyleSection)
    _register(HomeLifestyleTile)
    _register(HomePromoBanner)
    _register(HomeCategorySpotlightSection)
    _register(HomeCategorySpotlightTile)
    _register(HomeLovedBySection)
    _register(HomeLovedByQuote)
    _register(HomeTrustStripSection)
    _register(HomeTrustStripItem)
    _register(HomeValuePropSection)
    _register(HomeValuePropItem)
    _register(HomeGallerySection)
    _register(HomeGalleryItem)
