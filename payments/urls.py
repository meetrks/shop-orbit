from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("razorpay/webhook/", views.razorpay_webhook, name="razorpay_webhook"),
    path("<str:order_number>/verify/", views.verify_payment, name="verify_payment"),
]
