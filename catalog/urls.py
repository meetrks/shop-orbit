from django.urls import path

from . import views

app_name = "catalog"

urlpatterns = [
    path("catalog/", views.product_list, name="product_list"),
    path("catalog/product/<slug:product_slug>/", views.product_detail, name="product_detail"),
    path(
        "catalog/product/<slug:product_slug>/review/",
        views.submit_review,
        name="submit_review",
    ),
    path(
        "catalog/product/<slug:product_slug>/barcode.png",
        views.product_barcode,
        name="product_barcode",
    ),
    path(
        "catalog/product/<slug:product_slug>/variant/<int:variant_id>/barcode.png",
        views.variant_barcode,
        name="variant_barcode",
    ),
    path(
        "catalog/product/<slug:product_slug>/stock-alert/",
        views.request_stock_alert,
        name="request_stock_alert",
    ),
    path("catalog/<slug:department_slug>/", views.department_detail, name="department_detail"),
    path(
        "catalog/<slug:department_slug>/<slug:category_slug>/",
        views.category_detail,
        name="category_detail",
    ),
    path(
        "catalog/<slug:department_slug>/<slug:category_slug>/<slug:subcategory_slug>/",
        views.subcategory_detail,
        name="subcategory_detail",
    ),
]
