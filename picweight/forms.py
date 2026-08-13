"""Upload form for the PicWeight listing-image standardization tool."""

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError

from catalog.models import Product

from .models import SupplierImageJob

ACCEPTED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


class SupplierImageUploadForm(forms.ModelForm):
    class Meta:
        model = SupplierImageJob
        fields = ["source_image", "product"]
        labels = {
            "source_image": "Supplier / product photo",
            "product": "Link to a product (optional)",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        self.fields["product"].required = False

    def clean_source_image(self):
        image = self.cleaned_data["source_image"]

        max_size = getattr(settings, "PICWEIGHT_MAX_UPLOAD_SIZE", 8 * 1024 * 1024)
        if image.size > max_size:
            raise ValidationError(
                f"That file is too large. Please upload a photo under {max_size // (1024 * 1024)}MB."
            )

        content_type = getattr(image, "content_type", None)
        if content_type and content_type not in ACCEPTED_CONTENT_TYPES:
            raise ValidationError("Please upload a JPEG, PNG, or WEBP photo.")

        return image
