"""Purchase Order PDF rendering — renders a PurchaseOrder as a printable document."""

from __future__ import annotations

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from test_tenant_app.models import PurchaseOrder

_STYLES = getSampleStyleSheet()
_HEADER_BG = colors.HexColor("#1f2937")
_ROW_ALT_BG = colors.HexColor("#f9fafb")
_GRID_COLOR = colors.HexColor("#d1d5db")


def render_purchase_order_pdf(po: PurchaseOrder) -> bytes:
    """Render a PurchaseOrder as a one-page PDF and return its bytes."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        title=f"Purchase Order {po.order_id}",
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )
    doc.build(_story(po))
    return buffer.getvalue()


def _story(po: PurchaseOrder) -> list:
    story = [
        Paragraph("Purchase Order", _STYLES["Title"]),
        Paragraph(f"Order {po.order_id} &middot; {po.status}", _STYLES["Heading3"]),
        Spacer(1, 0.2 * inch),
        _summary_table(po),
        Spacer(1, 0.3 * inch),
        Paragraph("Line Items", _STYLES["Heading2"]),
        Spacer(1, 0.1 * inch),
        _line_items_table(po),
        Spacer(1, 0.2 * inch),
        _totals_table(po),
    ]
    if po.rejection_reason:
        story += [
            Spacer(1, 0.2 * inch),
            Paragraph(f"Rejection reason: {po.rejection_reason}", _STYLES["Normal"]),
        ]
    return story


def _summary_table(po: PurchaseOrder) -> Table:
    rows = [
        ["Supplier", po.supplier_name or po.supplier_id],
        ["Supplier contact", po.supplier_contact_email or "—"],
        ["Requisition", po.requisition_id],
        ["Award", po.award_id or "—"],
        ["Received", po.received_at.strftime("%Y-%m-%d %H:%M UTC")],
    ]
    table = Table(rows, colWidths=[1.7 * inch, 4.3 * inch])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _line_items_table(po: PurchaseOrder) -> Table:
    header = ["SKU", "Description", "Qty", "Unit Price", "Total"]
    rows = [header] + [
        [
            li.sku or "—",
            li.name,
            str(li.quantity),
            f"${li.unit_price:,.2f}",
            f"${li.total:,.2f}",
        ]
        for li in po.line_items
    ]
    table = Table(rows, colWidths=[1.3 * inch, 2.6 * inch, 0.6 * inch, 0.9 * inch, 1.0 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.5, _GRID_COLOR),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _ROW_ALT_BG]),
            ]
        )
    )
    return table


def _totals_table(po: PurchaseOrder) -> Table:
    rows = [["Total value", f"${po.total_value:,.2f}"]]
    table = Table(rows, colWidths=[5.3 * inch, 1.4 * inch])
    table.setStyle(
        TableStyle(
            [
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTNAME", (0, 0), (0, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 12),
            ]
        )
    )
    return table
