"""
Persistence for PicWeight jobs: one uploaded supplier photo plus the three
generated listing-image variants.
"""

from django.conf import settings
from django.db import models

from catalog.models import Product
from common.models import TimeStampedModel


class SupplierImageJob(TimeStampedModel):
    """One run of the PicWeight pipeline against a single uploaded photo."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSED = "processed", "Processed"
        FAILED = "failed", "Failed"

    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="picweight_jobs")
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="picweight_jobs",
        help_text="Optionally link this processed photo set to one of your product listings.",
    )

    source_image = models.ImageField(upload_to="picweight/uploads/%Y/%m/")

    cyan_frame_image = models.ImageField(upload_to="picweight/processed/%Y/%m/", blank=True)
    blue_frame_image = models.ImageField(upload_to="picweight/processed/%Y/%m/", blank=True)
    ultra_zoom_image = models.ImageField(upload_to="picweight/processed/%Y/%m/", blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    error_message = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"PicWeight job #{self.pk} by {self.uploaded_by.email} ({self.status})"
