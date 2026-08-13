from django.urls import path

from . import views

app_name = "fulfillment"

urlpatterns = [
    path("invoice/<str:order_number>/", views.invoice_download, name="invoice_download"),
    path("packing-slip/<str:order_number>/", views.packing_slip_download, name="packing_slip_download"),
]
