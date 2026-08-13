from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import Address, User


class AddressInline(admin.TabularInline):
    model = Address
    extra = 0
    fields = ["label", "recipient_name", "phone_number", "address_line1", "city", "state", "postal_code", "is_default"]


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """
    Admin configuration for the email-based custom User model. Rebuilds
    Django's default UserAdmin fieldsets around `email` instead of
    `username`.
    """

    ordering = ["-created_at"]
    list_display = [
        "email",
        "full_name",
        "is_active",
        "is_staff",
        "created_at",
    ]
    list_filter = ["is_active", "is_staff", "is_superuser"]
    search_fields = ["email", "full_name", "phone_number"]
    readonly_fields = ["created_at", "updated_at", "last_login"]
    inlines = [AddressInline]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("full_name", "phone_number")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "full_name",
                    "password1",
                    "password2",
                ),
            },
        ),
    )
