"""Storage for inbound contact-form submissions and the homepage builder."""

from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from accounts.models import phone_number_validator
from common.models import TimeStampedModel


class ContactMessage(TimeStampedModel):
    full_name = models.CharField(max_length=150)
    email = models.EmailField()
    phone_number = models.CharField(max_length=16, validators=[phone_number_validator])
    subject = models.CharField(max_length=150)
    message = models.TextField()
    is_resolved = models.BooleanField(default=False, help_text="Mark once this enquiry has been followed up.")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.subject} — {self.full_name}"


class HomeBanner(TimeStampedModel):
    """One full-width hero block on the homepage, staff-managed from admin."""

    heading = models.CharField(max_length=150)
    subheading = models.CharField(max_length=255, blank=True)
    image = models.ImageField(
        upload_to="home/banners/",
        blank=True,
        help_text="Leave blank to fall back to the site logo / initials badge.",
    )
    cta_text = models.CharField(max_length=50, blank=True, default="Shop Now")
    cta_url = models.CharField(
        max_length=255,
        blank=True,
        help_text="Where the call-to-action button links, e.g. /catalog/ or a full URL.",
    )
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]

    def __str__(self):
        return self.heading


class HomeSection(TimeStampedModel):
    """
    One curated product shelf on the homepage. `source` controls where its
    products come from — a staff-picked list, the newest active listings
    site-wide, or the newest active listings from one department — so the
    homepage can be reorganized entirely from admin, with no template or
    code changes, as the catalog grows or changes vertical.
    """

    class Source(models.TextChoices):
        MANUAL = "manual", "Manually curated products"
        LATEST = "latest", "Latest products (site-wide)"
        DEPARTMENT = "department", "Latest products from a department"

    title = models.CharField(max_length=150)
    subtitle = models.CharField(max_length=255, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.LATEST)
    department = models.ForeignKey(
        "catalog.Department",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Required when source is 'Latest products from a department'.",
    )
    limit = models.PositiveSmallIntegerField(
        default=8, help_text="Maximum number of products to show in this section."
    )
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]

    def __str__(self):
        return self.title


class HomeSectionProduct(TimeStampedModel):
    """One staff-picked product within a manually curated HomeSection."""

    section = models.ForeignKey(HomeSection, on_delete=models.CASCADE, related_name="section_products")
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]
        unique_together = [("section", "product")]

    def __str__(self):
        return f"{self.product.title} in {self.section.title}"


class HomeTestimonialSection(TimeStampedModel):
    """
    One customer-testimonials carousel on the homepage, staff-managed —
    same shape as HomeSection (title/subtitle/source/is_active/
    display_order), but sourcing catalog.Review rows instead of products.
    `source` controls whether testimonials are staff-picked (see
    HomeTestimonialSectionReview) or pulled automatically from the most
    recent verified-purchase reviews meeting `minimum_rating` that also
    have written feedback — a star rating alone doesn't make a quotable
    testimonial.
    """

    class Source(models.TextChoices):
        MANUAL = "manual", "Manually curated reviews"
        LATEST = "latest", "Latest high-rated reviews (site-wide)"

    title = models.CharField(max_length=150, default="What Our Customers Say")
    subtitle = models.CharField(max_length=255, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.LATEST)
    minimum_rating = models.PositiveSmallIntegerField(
        default=4,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="Only used when source is 'Latest' — reviews below this star rating are excluded.",
    )
    limit = models.PositiveSmallIntegerField(
        default=8, help_text="Maximum number of testimonials to show in this section."
    )
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]

    def __str__(self):
        return self.title


class HomeTestimonialSectionReview(TimeStampedModel):
    """One staff-picked review within a manually curated HomeTestimonialSection."""

    section = models.ForeignKey(HomeTestimonialSection, on_delete=models.CASCADE, related_name="section_reviews")
    review = models.ForeignKey("catalog.Review", on_delete=models.CASCADE)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]
        unique_together = [("section", "review")]

    def __str__(self):
        return f"{self.review} in {self.section.title}"


class HomePriceTier(TimeStampedModel):
    """
    One "Shop by Price" quick-filter link on the homepage — e.g. "Under
    Rs 99". Links straight into catalog.views.product_list's existing
    ?max_price= query param (see catalog.views._apply_sort_and_filters),
    so no new filtering logic is needed on the catalog side.
    """

    label = models.CharField(max_length=50, help_text='e.g. "Under Rs 99"')
    max_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Products priced at or below this amount are shown when a buyer clicks this tier.",
    )
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "max_price"]

    def __str__(self):
        return self.label
