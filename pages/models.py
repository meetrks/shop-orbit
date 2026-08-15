"""Storage for inbound contact-form submissions and the homepage builder."""

from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from accounts.models import phone_number_validator
from common.models import TimeStampedModel

from .icons import ICON_CHOICES

# Shared by every "singleton" homepage section below (Lifestyle,
# CategorySpotlight, LovedBy, TrustStrip, ValueProp, Gallery, and
# HomePromoBanner): only the first active row (by display_order) renders,
# so staff can stage a replacement inactive and cut over by flipping
# is_active on both rows, without losing the old one's content/history.
_SINGLETON_ACTIVE_HELP = (
    "Only the first active instance (by display order) is shown on the homepage — "
    "use this to stage a new version before cutting over, without deleting the old one."
)


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
    """
    One full-width banner block on the homepage, staff-managed from admin.
    `placement` controls where it renders — the hero at the top of the
    page, or a closing call-to-action banner just above the footer. Both
    placements share the same fields (heading/subheading/image/CTA); only
    the template treatment differs, so this is one model with two render
    slots rather than two near-identical models.
    """

    class Placement(models.TextChoices):
        HERO = "hero", "Hero (top of homepage)"
        CLOSING = "closing", "Closing CTA (above footer)"

    placement = models.CharField(max_length=20, choices=Placement.choices, default=Placement.HERO)
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


class HomeLifestyleSection(TimeStampedModel):
    """ "Shop Your Look" — a row of lifestyle tiles (e.g. For Everyday, For
    Festive), each linking into a filtered product view. See
    _SINGLETON_ACTIVE_HELP for how is_active is used here."""

    title = models.CharField(max_length=150, default="Shop Your Look")
    subtitle = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True, help_text=_SINGLETON_ACTIVE_HELP)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]

    def __str__(self):
        return self.title


class HomeLifestyleTile(TimeStampedModel):
    """One tile within a HomeLifestyleSection, e.g. "For Everyday"."""

    section = models.ForeignKey(HomeLifestyleSection, on_delete=models.CASCADE, related_name="tiles")
    image = models.ImageField(upload_to="home/lifestyle/")
    label = models.CharField(max_length=60, help_text='e.g. "For Everyday"')
    cta_text = models.CharField(max_length=30, blank=True, default="Shop Now")
    cta_url = models.CharField(max_length=255, blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]

    def __str__(self):
        return f"{self.label} in {self.section.title}"


class HomePromoBanner(TimeStampedModel):
    """
    A single wide promotional banner (e.g. "The Gold Look. Without The
    Gold Price."), with up to three short bullet points and one CTA. Flat
    — no child model, since a promo banner's bullets are a fixed handful
    of short lines, not an open-ended list worth a separate table.
    """

    heading = models.CharField(max_length=150)
    subheading = models.CharField(max_length=255, blank=True)
    bullet_1 = models.CharField(max_length=120, blank=True)
    bullet_2 = models.CharField(max_length=120, blank=True)
    bullet_3 = models.CharField(
        max_length=120, blank=True, help_text="Leave any bullet blank to show fewer than three."
    )
    image = models.ImageField(upload_to="home/promo/", blank=True)
    cta_text = models.CharField(max_length=50, blank=True, default="Shop Now")
    cta_url = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True, help_text=_SINGLETON_ACTIVE_HELP)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]

    def __str__(self):
        return self.heading

    @property
    def bullets(self):
        return [bullet for bullet in (self.bullet_1, self.bullet_2, self.bullet_3) if bullet]


class HomeCategorySpotlightSection(TimeStampedModel):
    """
    "Shop Categories" — a curated row of category tiles with custom
    photography, distinct from the auto-generated, all-subcategories
    Shop by Category carousel (see catalog.icons / pages.views.home's
    shop_by_categories) — this is "hand-picked highlights", that one is
    "browse everything".
    """

    title = models.CharField(max_length=150, default="Shop Categories")
    subtitle = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True, help_text=_SINGLETON_ACTIVE_HELP)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]

    def __str__(self):
        return self.title


class HomeCategorySpotlightTile(TimeStampedModel):
    """One tile within a HomeCategorySpotlightSection, e.g. "Earrings"."""

    section = models.ForeignKey(HomeCategorySpotlightSection, on_delete=models.CASCADE, related_name="tiles")
    image = models.ImageField(upload_to="home/category_spotlight/")
    label = models.CharField(max_length=60, help_text='e.g. "Earrings"')
    tagline = models.CharField(max_length=120, blank=True, help_text='e.g. "Discover your next favourite pair"')
    link_url = models.CharField(max_length=255, blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]

    def __str__(self):
        return f"{self.label} in {self.section.title}"


class HomeLovedBySection(TimeStampedModel):
    """
    "Loved by Our Customers" — a marketing rating badge plus a handful of
    staff-typed quote cards. Deliberately not catalog.Review-backed
    (unlike HomeTestimonialSection, which sources real verified-purchase
    reviews) — rating_value/rating_count_label are a marketing figure
    staff set directly (e.g. "4.8/5, 500+ Happy Customers"), which won't
    generally match the literal count of Review rows in the database.
    """

    title = models.CharField(max_length=150, default="Loved by Our Customers")
    subtitle = models.CharField(max_length=255, blank=True)
    rating_value = models.DecimalField(
        max_digits=2,
        decimal_places=1,
        default=Decimal("4.8"),
        validators=[MinValueValidator(Decimal("0.0")), MaxValueValidator(Decimal("5.0"))],
        help_text='e.g. 4.8 — shown as "4.8/5".',
    )
    rating_count_label = models.CharField(
        max_length=60, blank=True, default="500+ Happy Customers", help_text='e.g. "500+ Happy Customers"'
    )
    is_active = models.BooleanField(default=True, help_text=_SINGLETON_ACTIVE_HELP)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]

    def __str__(self):
        return self.title


class HomeLovedByQuote(TimeStampedModel):
    """One staff-typed customer quote within a HomeLovedBySection."""

    section = models.ForeignKey(HomeLovedBySection, on_delete=models.CASCADE, related_name="quotes")
    quote_text = models.TextField()
    customer_name = models.CharField(max_length=100)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]

    def __str__(self):
        return f"Quote from {self.customer_name} in {self.section.title}"


class HomeTrustStripSection(TimeStampedModel):
    """A row of small reassurance badges (Quality Checked, Secure
    Payments, Easy Returns, Fast Shipping). title/subtitle are optional
    since the reference design shows no visible heading for this strip."""

    title = models.CharField(max_length=150, blank=True)
    subtitle = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True, help_text=_SINGLETON_ACTIVE_HELP)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]

    def __str__(self):
        return self.title or "Trust strip"


class HomeTrustStripItem(TimeStampedModel):
    """One icon + label badge within a HomeTrustStripSection."""

    section = models.ForeignKey(HomeTrustStripSection, on_delete=models.CASCADE, related_name="items")
    icon = models.CharField(max_length=30, choices=ICON_CHOICES, default="shield-check")
    label = models.CharField(max_length=60, help_text='e.g. "Quality Checked"')
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]

    def __str__(self):
        return f"{self.label} in {self.section}"


class HomeValuePropSection(TimeStampedModel):
    """ "Why AVR Collections" — a row of icon + title + description value
    prop cards."""

    title = models.CharField(max_length=150, default="Why AVR Collections")
    subtitle = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True, help_text=_SINGLETON_ACTIVE_HELP)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]

    def __str__(self):
        return self.title


class HomeValuePropItem(TimeStampedModel):
    """One value-prop card within a HomeValuePropSection."""

    section = models.ForeignKey(HomeValuePropSection, on_delete=models.CASCADE, related_name="items")
    icon = models.CharField(max_length=30, choices=ICON_CHOICES, default="gem")
    title = models.CharField(max_length=80)
    description = models.TextField()
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]

    def __str__(self):
        return f"{self.title} in {self.section.title}"


class HomeGallerySection(TimeStampedModel):
    """
    "Behind the AVR" — a small photo gallery (packing, photoshoots, new
    arrivals) plus an optional Instagram follow link. Images only for
    now — video upload/hosting would need transcoding and storage
    infrastructure this feature doesn't need; a gallery item's link_url
    can point at an Instagram Reel for anything that's actually a video.
    """

    title = models.CharField(max_length=150, default="Behind the AVR")
    subtitle = models.CharField(max_length=255, blank=True)
    instagram_url = models.CharField(max_length=255, blank=True, help_text="Link for the 'Follow Our Journey' button.")
    is_active = models.BooleanField(default=True, help_text=_SINGLETON_ACTIVE_HELP)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]

    def __str__(self):
        return self.title


class HomeGalleryItem(TimeStampedModel):
    """One photo within a HomeGallerySection."""

    section = models.ForeignKey(HomeGallerySection, on_delete=models.CASCADE, related_name="items")
    image = models.ImageField(upload_to="home/gallery/")
    caption = models.CharField(max_length=150, blank=True)
    link_url = models.CharField(
        max_length=255, blank=True, help_text="Optional — e.g. a link to the Instagram post/reel for this item."
    )
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]

    def __str__(self):
        return f"{self.caption or 'Photo'} in {self.section.title}"
