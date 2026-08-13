from django.urls import path

from . import views

app_name = "pages"

urlpatterns = [
    path("", views.home, name="home"),
    path("privacy-policy/", views.privacy_policy, name="privacy_policy"),
    path("terms-and-conditions/", views.terms_conditions, name="terms_conditions"),
    path("shipping-policy/", views.shipping_policy, name="shipping_policy"),
    path("return-refund-policy/", views.return_refund_policy, name="return_refund_policy"),
    path("cancellation-policy/", views.cancellation_policy, name="cancellation_policy"),
    path("contact/", views.contact, name="contact"),
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path("manifest.json", views.manifest_json, name="manifest"),
    path("sw.js", views.service_worker, name="service_worker"),
]
