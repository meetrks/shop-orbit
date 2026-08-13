"""
PicWeight image-processing pipeline.

PicWeight is a listing-photo standardization utility for staff. Supplier
product photos rarely arrive on a clean, uniform background at a
consistent size, which makes a storefront's product grid look inconsistent
and can fall outside the plain-background / minimum-fill guidelines that
most marketplaces publish for listing photography. This module takes a
single uploaded photo, isolates the product item from its background,
centers it on a clean white square canvas, and exports three ready-to-use
presentation variants:

* ``cyan_frame``  -- product centered on white, with a 50px #00FFFF border.
* ``blue_frame``  -- product centered on white, with a 60px #0000FF border.
* ``ultra_zoom``  -- borderless, product scaled to fill 95% of the frame.

The pipeline is plain OpenCV + Pillow: grayscale the source, blur it,
threshold it to separate the product from its background, find the
product's bounding contour, crop with a 5% padding margin, then compose
each output variant on a 1000x1000 canvas.
"""

import io
from dataclasses import dataclass

import cv2
import numpy as np
from django.core.files.base import ContentFile
from PIL import Image, ImageOps

CANVAS_SIZE = 1000

CYAN_FRAME_BORDER_PX = 50
CYAN_FRAME_COLOR = (0, 255, 255)

BLUE_FRAME_BORDER_PX = 60
BLUE_FRAME_COLOR = (0, 0, 255)

FRAMED_FILL_RATIO = 0.90
ULTRA_ZOOM_FILL_RATIO = 0.95

CROP_BUFFER_RATIO = 0.05


class PicWeightProcessingError(ValueError):
    """Raised when an uploaded file cannot be turned into listing images."""


@dataclass
class ListingVariant:
    key: str
    filename_suffix: str
    content: ContentFile


def _read_upload_as_bgr(uploaded_file) -> np.ndarray:
    """Decodes a Django uploaded file into an OpenCV BGR image array."""
    uploaded_file.seek(0)
    raw_bytes = uploaded_file.read()
    buffer = np.frombuffer(raw_bytes, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise PicWeightProcessingError("That file could not be read as an image. Please upload a JPEG or PNG photo.")
    return image


def _locate_subject_bounding_box(bgr_image: np.ndarray) -> tuple[int, int, int, int]:
    """
    Isolates the product from its background and returns a padded
    (x, y, width, height) bounding box in the source image's coordinates.

    Falls back to the full image when no clear subject contour is found
    (e.g. a photo that is already tightly cropped or has a busy
    background), so the pipeline never hard-fails on an unusual photo.
    """
    height, width = bgr_image.shape[:2]

    grayscale = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(grayscale, (5, 5), 0)
    _threshold_value, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    contours, _hierarchy = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        return 0, 0, width, height

    largest_contour = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(largest_contour)

    if w == 0 or h == 0:
        return 0, 0, width, height

    margin_x = int(w * CROP_BUFFER_RATIO)
    margin_y = int(h * CROP_BUFFER_RATIO)

    x0 = max(x - margin_x, 0)
    y0 = max(y - margin_y, 0)
    x1 = min(x + w + margin_x, width)
    y1 = min(y + h + margin_y, height)

    return x0, y0, x1 - x0, y1 - y0


def _crop_subject(bgr_image: np.ndarray) -> Image.Image:
    x, y, w, h = _locate_subject_bounding_box(bgr_image)
    cropped_bgr = bgr_image[y : y + h, x : x + w]
    cropped_rgb = cv2.cvtColor(cropped_bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(cropped_rgb)


def _compose_on_canvas(
    subject: Image.Image,
    canvas_size: int = CANVAS_SIZE,
    border_width: int = 0,
    border_color: tuple[int, int, int] | None = None,
    fill_ratio: float = FRAMED_FILL_RATIO,
) -> Image.Image:
    """
    Centers `subject` on a clean white square canvas of `canvas_size`,
    scaled so its longest edge fills `fill_ratio` of the usable area
    (the canvas minus any border), then optionally wraps the result in a
    solid-color border of `border_width` pixels.
    """
    usable_size = canvas_size - (2 * border_width)

    target_dimension = max(int(usable_size * fill_ratio), 1)
    scale = min(target_dimension / subject.width, target_dimension / subject.height)
    new_width = max(int(subject.width * scale), 1)
    new_height = max(int(subject.height * scale), 1)
    resized_subject = subject.resize((new_width, new_height), Image.LANCZOS)

    inner_canvas = Image.new("RGB", (usable_size, usable_size), "white")
    paste_x = (usable_size - new_width) // 2
    paste_y = (usable_size - new_height) // 2
    inner_canvas.paste(resized_subject, (paste_x, paste_y))

    if border_width > 0 and border_color is not None:
        return ImageOps.expand(inner_canvas, border=border_width, fill=border_color)
    return inner_canvas


def _to_content_file(image: Image.Image, filename: str) -> ContentFile:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    buffer.seek(0)
    return ContentFile(buffer.read(), name=filename)


def generate_listing_variants(uploaded_file, base_filename: str) -> dict[str, ContentFile]:
    """
    Runs the full PicWeight pipeline on `uploaded_file` and returns a dict
    of {variant_key: ContentFile} ready to be assigned to model ImageFields.
    """
    bgr_image = _read_upload_as_bgr(uploaded_file)
    subject = _crop_subject(bgr_image)

    cyan_frame = _compose_on_canvas(
        subject,
        border_width=CYAN_FRAME_BORDER_PX,
        border_color=CYAN_FRAME_COLOR,
        fill_ratio=FRAMED_FILL_RATIO,
    )
    blue_frame = _compose_on_canvas(
        subject,
        border_width=BLUE_FRAME_BORDER_PX,
        border_color=BLUE_FRAME_COLOR,
        fill_ratio=FRAMED_FILL_RATIO,
    )
    ultra_zoom = _compose_on_canvas(
        subject,
        border_width=0,
        border_color=None,
        fill_ratio=ULTRA_ZOOM_FILL_RATIO,
    )

    return {
        "cyan_frame": _to_content_file(cyan_frame, f"{base_filename}_cyan_frame.jpg"),
        "blue_frame": _to_content_file(blue_frame, f"{base_filename}_blue_frame.jpg"),
        "ultra_zoom": _to_content_file(ultra_zoom, f"{base_filename}_ultra_zoom.jpg"),
    }
