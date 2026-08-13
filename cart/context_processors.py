"""
Feeds the navbar's cart and wishlist badges (item counts), plus the set of
wishlisted product IDs used to render a filled/outline heart on product
cards, on every page.
"""


def cart_summary(request):
    if not request.user.is_authenticated:
        return {"nav_cart_item_count": 0, "nav_wishlist_item_count": 0, "wishlisted_product_ids": set()}

    cart = getattr(request.user, "cart", None)
    wishlist = getattr(request.user, "wishlist", None)
    wishlisted_ids = set(wishlist.items.values_list("product_id", flat=True)) if wishlist else set()
    return {
        "nav_cart_item_count": cart.total_items if cart else 0,
        "nav_wishlist_item_count": len(wishlisted_ids),
        "wishlisted_product_ids": wishlisted_ids,
    }
