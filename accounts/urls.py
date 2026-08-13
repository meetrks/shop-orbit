from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("accounts/register/", views.register, name="register"),
    path("accounts/login/", views.EmailLoginView.as_view(), name="login"),
    path("accounts/logout/", views.logout_view, name="logout"),
    path("profile/", views.profile, name="profile"),
    path("orders/", views.order_list, name="orders"),
    path("addresses/", views.address_list, name="addresses"),
    path("addresses/add/", views.address_add, name="address_add"),
    path("addresses/<int:pk>/edit/", views.address_edit, name="address_edit"),
    path("addresses/<int:pk>/delete/", views.address_delete, name="address_delete"),
    path("addresses/<int:pk>/default/", views.address_set_default, name="address_set_default"),
    path("store-dashboard/", views.store_dashboard, name="store_dashboard"),
    path("store-dashboard/products/", views.store_dashboard_products, name="store_dashboard_products"),
    path("store-dashboard/orders/", views.store_dashboard_orders, name="store_dashboard_orders"),
    path(
        "store-dashboard/orders/<str:order_number>/",
        views.store_dashboard_order_detail,
        name="store_dashboard_order_detail",
    ),
    path(
        "store-dashboard/orders/<str:order_number>/accept/",
        views.store_dashboard_order_accept,
        name="store_dashboard_order_accept",
    ),
    path(
        "store-dashboard/orders/<str:order_number>/cancel/",
        views.store_dashboard_order_cancel,
        name="store_dashboard_order_cancel",
    ),
    path(
        "store-dashboard/orders/<str:order_number>/mark-packed/",
        views.store_dashboard_order_mark_packed,
        name="store_dashboard_order_mark_packed",
    ),
    path(
        "store-dashboard/payments/<int:payment_id>/refund/",
        views.store_dashboard_refund_payment,
        name="store_dashboard_refund_payment",
    ),
    path("store-dashboard/profitability/", views.profitability_report, name="profitability_report"),
]
