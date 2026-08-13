"""
Generates a printable Code128 barcode image for a product's SKU, on the
fly — nothing is stored on disk. Code128 is used (rather than EAN-13/UPC)
because it accepts the full alphanumeric SKU format already typed in,
with no separate numeric barcode field to assign or keep in sync.
"""

import io

import barcode
from barcode.writer import ImageWriter

_CODE128 = barcode.get_barcode_class("code128")


def generate_barcode_png(value):
    """Returns a BytesIO PNG of a Code128 barcode encoding `value`."""
    writer = ImageWriter()
    buffer = io.BytesIO()
    _CODE128(value, writer=writer).write(
        buffer,
        options={
            "module_height": 12.0,
            "font_size": 9,
            "text_distance": 3.0,
            "quiet_zone": 4.0,
            "write_text": True,
        },
    )
    buffer.seek(0)
    return buffer
