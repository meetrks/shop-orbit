from django.urls import path

from . import views

app_name = "picweight"

urlpatterns = [
    path("", views.upload, name="upload"),
    path("result/<int:job_id>/", views.result, name="result"),
]
