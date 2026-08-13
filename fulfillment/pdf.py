"""
PDF rendering for GST invoices and packing slips.

Uses reportlab exclusively — no wkhtmltopdf/WeasyPrint binary or system
font-rendering stack required, so PDF generation works the same in any
deployment environment. Amounts are prefixed "Rs." rather than the "₹"
glyph: reportlab's built-in Helvetica (a base-14 PDF font, no embedded
font file) has no glyph for U+20B9 and would render it as a blank box.
"""

from io import BytesIO

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from catalog.barcodes import generate_barcode_png

_styles = getSampleStyleSheet()
_title_style = ParagraphStyle("DocTitle", parent=_styles["Heading1"], fontSize=18, spaceAfter=4)
_small = ParagraphStyle("Small", parent=_styles["Normal"], fontSize=9, leading=12)
# Table cells hand reportlab a plain string, it doesn't wrap to the column
# width the way a Paragraph does — it just draws the text past the cell
# boundary, overlapping whatever's next to it. Product titles are long
# enough to hit that, so those cells use this style via Paragraph() instead.
_cell_style = ParagraphStyle("Cell", parent=_styles["Normal"], fontSize=8, leading=10)

_PAGE_KWARGS = dict(pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm, leftMargin=18 * mm, rightMargin=18 * mm)


def _shipping_address_lines(order):
    lines = [order.shipping_full_name, order.shipping_address_line1]
    if order.shipping_address_line2:
        lines.append(order.shipping_address_line2)
    lines.append(f"{order.shipping_city}, {order.shipping_state} {order.shipping_postal_code}")
    lines.append(order.shipping_country)
    return lines


def build_invoice_pdf(order, invoice) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, **_PAGE_KWARGS)
    story = [Paragraph("Tax Invoice", _title_style), Paragraph(settings.SITE_LEGAL_NAME, _styles["Heading3"])]

    for line in [
        settings.COMPANY_ADDRESS_LINE1,
        settings.COMPANY_ADDRESS_LINE2,
        f"{settings.COMPANY_CITY}, {settings.COMPANY_STATE} {settings.COMPANY_POSTAL_CODE}".strip(", "),
        settings.COMPANY_COUNTRY,
    ]:
        if line:
            story.append(Paragraph(line, _small))
    if settings.COMPANY_GST_NUMBER:
        story.append(Paragraph(f"GSTIN: {settings.COMPANY_GST_NUMBER}", _small))
    story.append(Spacer(1, 8 * mm))

    meta_table = Table(
        [
            ["Invoice No.", invoice.invoice_number, "Invoice Date", invoice.issued_at.strftime("%d %b %Y")],
            ["Order No.", order.order_number, "Order Date", order.created_at.strftime("%d %b %Y")],
        ],
        colWidths=[28 * mm, 57 * mm, 28 * mm, 57 * mm],
    )
    meta_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph("Billed To", _styles["Heading4"]))
    for line in _shipping_address_lines(order):
        story.append(Paragraph(line, _small))
    story.append(Spacer(1, 6 * mm))

    is_interstate = invoice.is_interstate
    header = (
        ["#", "Item", "HSN", "Qty", "Taxable Value", "IGST", "Total"]
        if is_interstate
        else ["#", "Item", "HSN", "Qty", "Taxable Value", "CGST", "SGST", "Total"]
    )
    rows = [header]
    for idx, item in enumerate(order.items.all(), start=1):
        label = Paragraph(item.product_title + (f" ({item.variant_label})" if item.variant_label else ""), _cell_style)
        if is_interstate:
            rows.append(
                [
                    str(idx),
                    label,
                    item.hsn_code or "-",
                    str(item.quantity),
                    f"Rs. {item.taxable_value:.2f}",
                    f"Rs. {item.tax_amount:.2f}",
                    f"Rs. {item.line_total:.2f}",
                ]
            )
        else:
            half_tax = item.tax_amount / 2
            rows.append(
                [
                    str(idx),
                    label,
                    item.hsn_code or "-",
                    str(item.quantity),
                    f"Rs. {item.taxable_value:.2f}",
                    f"Rs. {half_tax:.2f}",
                    f"Rs. {half_tax:.2f}",
                    f"Rs. {item.line_total:.2f}",
                ]
            )

    col_widths = (
        [8 * mm, 60 * mm, 16 * mm, 10 * mm, 26 * mm, 22 * mm, 28 * mm]
        if is_interstate
        else [8 * mm, 48 * mm, 14 * mm, 9 * mm, 24 * mm, 19 * mm, 19 * mm, 24 * mm]
    )
    items_table = Table(rows, colWidths=col_widths, repeatRows=1)
    items_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(items_table)
    story.append(Spacer(1, 6 * mm))

    totals_rows = [["Taxable Amount", f"Rs. {invoice.taxable_amount:.2f}"]]
    if is_interstate:
        totals_rows.append(["IGST", f"Rs. {invoice.igst_amount:.2f}"])
    else:
        totals_rows.append(["CGST", f"Rs. {invoice.cgst_amount:.2f}"])
        totals_rows.append(["SGST", f"Rs. {invoice.sgst_amount:.2f}"])
    if order.discount_amount:
        totals_rows.append(["Discount", f"-Rs. {order.discount_amount:.2f}"])
    totals_rows.append(["Total Payable", f"Rs. {invoice.total_amount:.2f}"])
    totals_table = Table(totals_rows, colWidths=[140 * mm, 30 * mm])
    totals_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.75, colors.black),
            ]
        )
    )
    story.append(totals_table)
    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph("This is a system-generated invoice and does not require a signature.", _small))

    doc.build(story)
    return buffer.getvalue()


def build_packing_slip_pdf(order) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, **_PAGE_KWARGS)
    story = [Paragraph("Packing Slip", _title_style), Paragraph(f"Order {order.order_number}", _styles["Heading3"])]

    barcode_buffer = generate_barcode_png(order.order_number)
    story.append(Image(barcode_buffer, width=60 * mm, height=20 * mm))
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph("Ship To", _styles["Heading4"]))
    for line in _shipping_address_lines(order):
        story.append(Paragraph(line, _small))
    story.append(Paragraph(f"Phone: {order.shipping_phone_number}", _small))
    story.append(Spacer(1, 8 * mm))

    rows = [["Item", "SKU", "Qty"]]
    for item in order.items.all():
        label = Paragraph(item.product_title + (f" ({item.variant_label})" if item.variant_label else ""), _cell_style)
        rows.append([label, item.variant_sku or item.product_sku, str(item.quantity)])
    table = Table(rows, colWidths=[110 * mm, 40 * mm, 20 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ]
        )
    )
    story.append(table)

    if order.customer_note:
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph("Customer note:", _styles["Heading4"]))
        story.append(Paragraph(order.customer_note, _small))

    doc.build(story)
    return buffer.getvalue()
