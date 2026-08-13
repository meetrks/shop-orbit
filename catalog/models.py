"""
Catalog taxonomy and product models.

The storefront routes products through a strict three-tier hierarchy —
Department -> Category -> Subcategory — so that navigation, URLs, and admin
filtering all stay predictable. Every level carries its own SEO-friendly
slug, auto-generated from its name if one isn't supplied explicitly.
"""

from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils.text import slugify

from common.models import TimeStampedModel


def unique_slugify(instance, base_value, slug_field_name="slug", queryset=None):
    """
    Generates a unique slug for `instance` by slugifying `base_value` and,
    on collision, appending an incrementing numeric suffix.
    """
    slug = slugify(base_value)
    ModelClass = instance.__class__
    if queryset is None:
        queryset = ModelClass._default_manager.all()
    if instance.pk:
        queryset = queryset.exclude(pk=instance.pk)

    unique_slug = slug
    counter = 2
    while queryset.filter(**{slug_field_name: unique_slug}).exists():
        unique_slug = f"{slug}-{counter}"
        counter += 1
    return unique_slug


class Department(TimeStampedModel):
    """Top tier of the taxonomy, e.g. Women, Men, Kids, Unisex."""

    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(max_length=60, unique=True, blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name = "Department"
        verbose_name_plural = "Departments"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, self.name)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("catalog:department_detail", kwargs={"department_slug": self.slug})


class Category(TimeStampedModel):
    """Second tier of the taxonomy, scoped to a single Department."""

    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=100, blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name = "Category"
        verbose_name_plural = "Categories"
        unique_together = [("department", "slug"), ("department", "name")]

    def __str__(self):
        return f"{self.department.name} / {self.name}"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(
                self,
                self.name,
                queryset=Category.objects.filter(department=self.department),
            )
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse(
            "catalog:category_detail",
            kwargs={"department_slug": self.department.slug, "category_slug": self.slug},
        )


class Subcategory(TimeStampedModel):
    """Third and final tier of the taxonomy; products attach here."""

    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="subcategories")
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=100, blank=True)
    icon = models.ImageField(
        upload_to="catalog/subcategory_icons/",
        blank=True,
        help_text="Shown on the homepage's Shop by Category section. Leave blank for a generated initial badge.",
    )
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name = "Subcategory"
        verbose_name_plural = "Subcategories"
        unique_together = [("category", "slug"), ("category", "name")]

    def __str__(self):
        return f"{self.category.department.name} / {self.category.name} / {self.name}"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(
                self,
                self.name,
                queryset=Subcategory.objects.filter(category=self.category),
            )
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse(
            "catalog:subcategory_detail",
            kwargs={
                "department_slug": self.category.department.slug,
                "category_slug": self.category.slug,
                "subcategory_slug": self.slug,
            },
        )


class Product(TimeStampedModel):
    """A single sellable product listing."""

    class StockStatus(models.TextChoices):
        IN_STOCK = "in_stock", "In stock"
        LOW_STOCK = "low_stock", "Low stock"
        OUT_OF_STOCK = "out_of_stock", "Out of stock"

    subcategory = models.ForeignKey(Subcategory, on_delete=models.PROTECT, related_name="products")
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    sku = models.CharField("SKU", max_length=64, unique=True, help_text="Unique stock keeping unit for tracking.")
    description = models.TextField(blank=True)

    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    discount_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Optional discounted price. Must be lower than the regular price.",
    )
    delivery_charge = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("50.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Folded into the displayed price per unit — never shown to buyers as a separate line item.",
    )
    other_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Folded into the displayed price per unit — never shown to buyers as a separate line item.",
    )
    cost_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text=(
            "What this product actually costs to buy in, per unit (COGS) — never shown to buyers. "
            "Used only for the profitability report (Store Dashboard). Left at 0 for products this "
            "hasn't been entered for yet, which the report shows separately rather than treating as free."
        ),
    )

    stock_count = models.PositiveIntegerField(default=0, help_text="Total units physically on hand.")
    reserved_count = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Units held for in-flight checkouts (payment not yet captured). "
            "Not directly editable — see inventory.services."
        ),
    )
    low_stock_threshold = models.PositiveIntegerField(
        default=5, help_text="Stock at or below this number is flagged as low in the admin."
    )
    default_supplier = models.ForeignKey(
        "inventory.Supplier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
        help_text="Used to pre-fill a restock purchase order for this product.",
    )

    hsn_code = models.CharField(
        "HSN code",
        max_length=8,
        blank=True,
        help_text="GST HSN/SAC code for this product, printed on invoices. Leave blank if not yet classified.",
    )
    gst_rate = models.DecimalField(
        "GST rate (%)",
        max_digits=4,
        decimal_places=2,
        default=Decimal("5.00"),
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("28.00"))],
        help_text=(
            "Total GST percentage bundled into this product's price (e.g. 5 for 5%). "
            "Invoices split this into CGST+SGST or IGST depending on the buyer's state."
        ),
    )

    thumbnail = models.ImageField(upload_to="products/thumbnails/")

    is_active = models.BooleanField(default=True)

    meta_title = models.CharField(
        max_length=70,
        blank=True,
        help_text=(
            "Overrides the page <title> / social share title for this product. "
            "Falls back to the product title if left blank."
        ),
    )
    meta_description = models.CharField(
        max_length=160,
        blank=True,
        help_text=(
            "Overrides the search-result and social share description for this product. "
            "Falls back to the product description if left blank — set this explicitly if the "
            "description isn't suitable for search snippets (e.g. informal or non-English copy)."
        ),
    )
    meta_keywords = models.CharField(
        max_length=255,
        blank=True,
        help_text="Comma-separated search keywords for this product. Falls back to the site default if left blank.",
    )

    search_vector = SearchVectorField(
        null=True,
        blank=True,
        editable=False,
        help_text=(
            "Weighted full-text index (title/keywords/description), kept in sync "
            "by catalog.signals. Not user-editable."
        ),
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            GinIndex(fields=["search_vector"], name="product_search_vector_gin"),
            GinIndex(fields=["title"], name="product_title_trgm_gin", opclasses=["gin_trgm_ops"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.sku})"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, self.title, queryset=Product.objects.all())
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("catalog:product_detail", kwargs={"product_slug": self.slug})

    @property
    def display_meta_title(self):
        return self.meta_title or self.title

    @property
    def display_meta_description(self):
        text = self.meta_description or self.description or self.title
        return text[:160]

    @property
    def _fees(self):
        return self.delivery_charge + self.other_fee

    @property
    def display_price(self):
        """The bundled price a buyer pays per unit: product price plus delivery/other fees, never broken out."""
        base = self.discount_price if self.discount_price else self.price
        return base + self._fees

    @property
    def full_price(self):
        """The bundled, non-discounted price — for striking through next to a discounted display_price."""
        return self.price + self._fees

    @property
    def discount_percent(self):
        if self.discount_price and self.price > 0:
            savings = self.price - self.discount_price
            return int((savings / self.price) * 100)
        return 0

    @property
    def available_to_sell(self):
        """
        On-hand stock minus what's currently held for other buyers'
        in-flight checkouts — this, not raw `stock_count`, is what
        determines whether this product can still be bought. See
        `inventory.services` for how reservations are made/released.
        """
        return max(self.stock_count - self.reserved_count, 0)

    @property
    def stock_status(self):
        if self.available_to_sell <= 0:
            return self.StockStatus.OUT_OF_STOCK
        if self.available_to_sell <= self.low_stock_threshold:
            return self.StockStatus.LOW_STOCK
        return self.StockStatus.IN_STOCK

    @property
    def is_low_stock(self):
        return 0 < self.available_to_sell <= self.low_stock_threshold

    @property
    def is_out_of_stock(self):
        return self.available_to_sell <= 0


class ProductVariant(TimeStampedModel):
    """
    A purchasable option of a Product — e.g. a specific size or color —
    with its own SKU, stock, and optional price override. Deliberately
    additive: a Product with no variants behaves exactly as it always has
    (its own sku/price/stock_count are the only purchasable thing), so
    existing single-SKU listings, carts, and orders are unaffected.

    Option values are stored as free-form `attributes` (e.g.
    {"Size": "M", "Color": "Red"}) rather than a fixed schema, so the same
    model works for any product type — clothing sizes, electronics
    storage/color, etc. — without per-category configuration.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    sku = models.CharField("SKU", max_length=64, unique=True, help_text="Unique stock keeping unit for this variant.")
    attributes = models.JSONField(
        default=dict,
        blank=True,
        help_text='Option values for this variant, e.g. {"Size": "M", "Color": "Red"}.',
    )

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Leave blank to use the base product's price.",
    )
    discount_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Leave blank to use the base product's discount price.",
    )

    stock_count = models.PositiveIntegerField(default=0, help_text="Total units physically on hand.")
    reserved_count = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Units held for in-flight checkouts (payment not yet captured). "
            "Not directly editable — see inventory.services."
        ),
    )
    low_stock_threshold = models.PositiveIntegerField(
        default=5, help_text="Stock at or below this number is flagged as low in the admin."
    )

    thumbnail = models.ImageField(
        upload_to="products/variants/",
        blank=True,
        help_text="Leave blank to use the base product's thumbnail.",
    )

    is_active = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]

    def __str__(self):
        return f"{self.product.title} — {self.label} ({self.sku})"

    @property
    def label(self):
        return ", ".join(f"{name}: {value}" for name, value in self.attributes.items()) or self.sku

    @property
    def effective_price(self):
        return self.price if self.price is not None else self.product.price

    @property
    def effective_discount_price(self):
        return self.discount_price if self.discount_price is not None else self.product.discount_price

    @property
    def display_price(self):
        """The bundled price a buyer pays per unit: variant price plus the product's delivery/other fees."""
        base = self.effective_discount_price if self.effective_discount_price else self.effective_price
        return base + self.product._fees

    @property
    def full_price(self):
        """The bundled, non-discounted price — for striking through next to a discounted display_price."""
        return self.effective_price + self.product._fees

    @property
    def discount_percent(self):
        if self.effective_discount_price and self.effective_price > 0:
            savings = self.effective_price - self.effective_discount_price
            return int((savings / self.effective_price) * 100)
        return 0

    @property
    def display_thumbnail(self):
        return self.thumbnail if self.thumbnail else self.product.thumbnail

    @property
    def available_to_sell(self):
        """On-hand stock minus what's held for other buyers' in-flight checkouts. See `Product.available_to_sell`."""
        return max(self.stock_count - self.reserved_count, 0)

    @property
    def stock_status(self):
        if self.available_to_sell <= 0:
            return Product.StockStatus.OUT_OF_STOCK
        if self.available_to_sell <= self.low_stock_threshold:
            return Product.StockStatus.LOW_STOCK
        return Product.StockStatus.IN_STOCK

    @property
    def is_low_stock(self):
        return 0 < self.available_to_sell <= self.low_stock_threshold

    @property
    def is_out_of_stock(self):
        return self.available_to_sell <= 0


class ProductImage(TimeStampedModel):
    """One additional gallery image belonging to a Product."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="gallery_images")
    image = models.ImageField(upload_to="products/gallery/")
    alt_text = models.CharField(max_length=200, blank=True)
    is_primary = models.BooleanField(default=False)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "created_at"]

    def __str__(self):
        return f"Image for {self.product.title}"


class Review(TimeStampedModel):
    """A buyer's star rating and optional written feedback on a product."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews")
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    title = models.CharField(max_length=120, blank=True)
    comment = models.TextField(blank=True)
    is_verified_purchase = models.BooleanField(
        default=False,
        help_text="Automatically set when the reviewer has a confirmed order for this product.",
    )

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("product", "user")]

    def __str__(self):
        return f"{self.rating}★ review of {self.product.title} by {self.user.email}"


class StockAlert(TimeStampedModel):
    """
    An email signup for "notify me when back in stock" on an out-of-stock
    product (or, if given, one specific variant of it). Open to anonymous
    visitors, not just logged-in buyers, matching the contact form.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="stock_alerts")
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="stock_alerts",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="stock_alerts",
    )
    email = models.EmailField()
    notified_at = models.DateTimeField(
        null=True, blank=True, help_text="Set automatically once a back-in-stock email has been sent."
    )

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("product", "variant", "email")]

    def __str__(self):
        suffix = f" ({self.variant.label})" if self.variant_id else ""
        return f"Stock alert for {self.product.title}{suffix} — {self.email}"
