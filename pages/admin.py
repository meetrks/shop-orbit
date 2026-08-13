from django.contrib import admin

from .models import (
    ContactMessage,
    HomeBanner,
    HomePriceTier,
    HomeSection,
    HomeSectionProduct,
    HomeTestimonialSection,
    HomeTestimonialSectionReview,
)


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ["subject", "full_name", "email", "phone_number", "is_resolved", "created_at"]
    list_filter = ["is_resolved", "created_at"]
    search_fields = ["full_name", "email", "phone_number", "subject", "message"]
    readonly_fields = ["full_name", "email", "phone_number", "subject", "message", "created_at", "updated_at"]
    list_editable = ["is_resolved"]
    date_hierarchy = "created_at"
    list_per_page = 50


@admin.register(HomeBanner)
class HomeBannerAdmin(admin.ModelAdmin):
    list_display = ["heading", "is_active", "display_order", "created_at"]
    list_editable = ["is_active", "display_order"]
    list_filter = ["is_active"]
    search_fields = ["heading", "subheading"]


class HomeSectionProductInline(admin.TabularInline):
    """Only used when the section's source is 'Manually curated products'."""

    model = HomeSectionProduct
    extra = 1
    autocomplete_fields = ["product"]


@admin.register(HomeSection)
class HomeSectionAdmin(admin.ModelAdmin):
    list_display = ["title", "source", "department", "limit", "is_active", "display_order"]
    list_editable = ["is_active", "display_order"]
    list_filter = ["source", "is_active"]
    search_fields = ["title", "subtitle"]
    autocomplete_fields = ["department"]
    inlines = [HomeSectionProductInline]


class HomeTestimonialSectionReviewInline(admin.TabularInline):
    """Only used when the section's source is 'Manually curated reviews'."""

    model = HomeTestimonialSectionReview
    extra = 1
    autocomplete_fields = ["review"]


@admin.register(HomeTestimonialSection)
class HomeTestimonialSectionAdmin(admin.ModelAdmin):
    list_display = ["title", "source", "minimum_rating", "limit", "is_active", "display_order"]
    list_editable = ["is_active", "display_order"]
    list_filter = ["source", "is_active"]
    search_fields = ["title", "subtitle"]
    inlines = [HomeTestimonialSectionReviewInline]


@admin.register(HomePriceTier)
class HomePriceTierAdmin(admin.ModelAdmin):
    list_display = ["label", "max_price", "is_active", "display_order"]
    list_editable = ["is_active", "display_order"]
    list_filter = ["is_active"]
    search_fields = ["label"]
