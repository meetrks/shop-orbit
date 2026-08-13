from django.urls import path

from . import views

app_name = "cart"

urlpatterns = [
    path("", views.cart_detail, name="cart_detail"),
    path("add/<int:product_id>/", views.add_to_cart, name="add_to_cart"),
    path("item/<int:item_id>/update/", views.update_cart_item, name="update_cart_item"),
    path("item/<int:item_id>/remove/", views.remove_cart_item, name="remove_cart_item"),
    path("checkout/", views.checkout, name="checkout"),
    path("checkout/<str:order_number>/pay/", views.checkout_payment, name="checkout_payment"),
    path("checkout/<str:order_number>/pay/retry/", views.payment_retry, name="payment_retry"),
    path("checkout/<str:order_number>/success/", views.payment_success, name="payment_success"),
    path("checkout/<str:order_number>/failed/", views.payment_failed, name="payment_failed"),
    path(
        "checkout/confirmation/<str:order_number>/",
        views.order_confirmation,
        name="order_confirmation",
    ),
    path("order/<str:order_number>/cancel/", views.cancel_order, name="cancel_order"),
    path("wishlist/", views.wishlist_detail, name="wishlist_detail"),
    path("wishlist/toggle/<int:product_id>/", views.toggle_wishlist, name="toggle_wishlist"),
]
