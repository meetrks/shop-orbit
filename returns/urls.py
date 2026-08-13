from django.urls import path

from . import views

app_name = "returns"

urlpatterns = [
    path("request/<str:order_number>/", views.request_return, name="request_return"),
    path("<int:pk>/cancel/", views.cancel_return_request, name="cancel_return_request"),
    path("<int:pk>/shipment/", views.update_return_shipment, name="update_return_shipment"),
]
