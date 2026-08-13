from django.contrib import admin

from .models import SupplierImageJob


@admin.register(SupplierImageJob)
class SupplierImageJobAdmin(admin.ModelAdmin):
    list_display = ["id", "uploaded_by", "product", "status", "created_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["uploaded_by__email", "uploaded_by__full_name", "product__title", "product__sku"]
    readonly_fields = [
        "uploaded_by",
        "source_image",
        "cyan_frame_image",
        "blue_frame_image",
        "ultra_zoom_image",
        "status",
        "error_message",
        "created_at",
        "updated_at",
    ]
    list_per_page = 50
