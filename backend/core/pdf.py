import io
from decimal import Decimal
from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


def format_rupees(paise: int) -> str:
    """
    Format paise into Indian rupee string representation with integer arithmetic.
    Always produces two decimal places.
    Examples: 787500 -> '7,875.00', 18750 -> '187.50', 5 -> '0.05', 0 -> '0.00'.
    Raises ValueError for negative numbers.
    """
    if not isinstance(paise, int) or paise < 0:
        raise ValueError(f"paise must be a non-negative integer, got {paise}")

    rupees = paise // 100
    rem = paise % 100
    s_rupees = str(rupees)

    if len(s_rupees) <= 3:
        formatted_int = s_rupees
    else:
        last3 = s_rupees[-3:]
        rem_digits = s_rupees[:-3]
        groups = []
        while len(rem_digits) > 2:
            groups.append(rem_digits[-2:])
            rem_digits = rem_digits[:-2]
        if rem_digits:
            groups.append(rem_digits)
        groups.reverse()
        formatted_int = ",".join(groups) + "," + last3

    return f"{formatted_int}.{rem:02d}"


def format_rate(gst_rate_bp: int) -> str:
    """
    Format basis points rate into percentage string using integer arithmetic.
    Examples: 500 -> '5%', 250 -> '2.5%', 1800 -> '18%'.
    """
    if not isinstance(gst_rate_bp, int) or gst_rate_bp < 0:
        raise ValueError(f"gst_rate_bp must be a non-negative integer, got {gst_rate_bp}")

    int_part = gst_rate_bp // 100
    rem = gst_rate_bp % 100

    if rem == 0:
        return f"{int_part}%"
    elif rem % 10 == 0:
        return f"{int_part}.{rem // 10}%"
    else:
        return f"{int_part}.{rem:02d}%"


def build_invoice_pdf(invoice) -> bytes:
    """
    Build a PDF tax invoice using ReportLab SimpleDocTemplate on A4.
    Uses built-in Helvetica font only and writes 'Rs' instead of symbol.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "InvoiceTitle",
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        alignment=1,  # Center
    )
    cancel_style = ParagraphStyle(
        "CancelTitle",
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        textColor=colors.red,
        alignment=1,
    )
    normal_style = ParagraphStyle(
        "NormalText",
        fontName="Helvetica",
        fontSize=9,
        leading=13,
    )
    bold_style = ParagraphStyle(
        "BoldText",
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=13,
    )
    footer_style = ParagraphStyle(
        "FooterText",
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        alignment=1,
        textColor=colors.dimgrey,
    )

    story = []

    # Title
    story.append(Paragraph("TAX INVOICE", title_style))
    story.append(Spacer(1, 4))

    # Cancelled banner if applicable
    if invoice.status == "CANCELLED":
        cn_number = getattr(invoice, "credit_note", None)
        cn_text = f"CANCELLED - Credit note {cn_number.number}" if cn_number else "CANCELLED"
        story.append(Paragraph(cn_text, cancel_style))
        story.append(Spacer(1, 6))

    # Seller & Invoice info block (side by side)
    factory_name = getattr(settings, "FACTORY_NAME", "Sample Snacks Pvt Ltd")
    factory_address = getattr(settings, "FACTORY_ADDRESS", "Coimbatore, Tamil Nadu")
    factory_gstin = getattr(settings, "FACTORY_GSTIN", "")
    factory_state_name = getattr(settings, "FACTORY_STATE_NAME", "")
    factory_state_code = getattr(settings, "FACTORY_STATE_CODE", "")

    seller_p = Paragraph(
        f"<b>{factory_name}</b><br/>"
        f"{factory_address}<br/>"
        f"GSTIN: {factory_gstin}<br/>"
        f"State: {factory_state_name} ({factory_state_code})",
        normal_style,
    )

    inv_date_str = invoice.invoice_date.strftime("%d-%m-%Y")
    invoice_p = Paragraph(
        f"<b>Invoice No:</b> {invoice.number}<br/>"
        f"<b>Date:</b> {inv_date_str}<br/>"
        f"<b>Financial Year:</b> {invoice.fy}",
        normal_style,
    )

    header_table = Table([[seller_p, invoice_p]], colWidths=[310, 213])
    header_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ])
    )
    story.append(header_table)
    story.append(Spacer(1, 8))

    # Buyer block
    buyer_gstin = invoice.customer_gstin if invoice.customer_gstin else "Unregistered"
    buyer_p = Paragraph(
        f"<b>Bill To:</b><br/>"
        f"<b>{invoice.customer_name}</b><br/>"
        f"GSTIN: {buyer_gstin}<br/>"
        f"Place of supply (state code): {invoice.customer_state_code}",
        normal_style,
    )
    buyer_table = Table([[buyer_p]], colWidths=[523])
    buyer_table.setStyle(
        TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BACKGROUND", (0, 0), (-1, -1), colors.whitesmoke),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ])
    )
    story.append(buyer_table)
    story.append(Spacer(1, 10))

    # Line table
    is_intra = (invoice.supply_type == "INTRA")
    if is_intra:
        table_headers = [
            "#",
            "Description",
            "HSN",
            "Qty",
            "Rate (Rs)",
            "Taxable (Rs)",
            "CGST %",
            "CGST (Rs)",
            "SGST %",
            "SGST (Rs)",
        ]
        col_widths = [22, 115, 42, 34, 55, 65, 45, 50, 45, 50]
    else:
        table_headers = [
            "#",
            "Description",
            "HSN",
            "Qty",
            "Rate (Rs)",
            "Taxable (Rs)",
            "IGST %",
            "IGST (Rs)",
        ]
        col_widths = [25, 145, 50, 38, 60, 75, 55, 75]

    table_data = [table_headers]

    for idx, line in enumerate(invoice.lines.all(), start=1):
        rate_str = format_rupees(line.unit_price_paise)
        taxable_str = format_rupees(line.taxable_paise)

        if is_intra:
            half_p = format_rate(line.gst_rate_bp // 2)
            cgst_str = format_rupees(line.cgst_paise)
            sgst_str = format_rupees(line.sgst_paise)
            table_data.append([
                str(idx),
                line.description,
                line.hsn_code,
                str(line.quantity),
                rate_str,
                taxable_str,
                half_p,
                cgst_str,
                half_p,
                sgst_str,
            ])
        else:
            igst_p = format_rate(line.gst_rate_bp)
            igst_str = format_rupees(line.igst_paise)
            table_data.append([
                str(idx),
                line.description,
                line.hsn_code,
                str(line.quantity),
                rate_str,
                taxable_str,
                igst_p,
                igst_str,
            ])

    items_table = Table(table_data, colWidths=col_widths)
    items_table.setStyle(
        TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("ALIGN", (1, 1), (1, -1), "LEFT"),  # Description left-aligned
            ("ALIGN", (4, 1), (-1, -1), "RIGHT"),  # Amounts right-aligned
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ])
    )
    story.append(items_table)
    story.append(Spacer(1, 8))

    # Totals block
    if is_intra:
        totals_data = [
            ["Taxable value:", format_rupees(invoice.taxable_paise)],
            ["CGST:", format_rupees(invoice.cgst_paise)],
            ["SGST:", format_rupees(invoice.sgst_paise)],
            ["Total (Rs):", format_rupees(invoice.total_paise)],
        ]
    else:
        totals_data = [
            ["Taxable value:", format_rupees(invoice.taxable_paise)],
            ["IGST:", format_rupees(invoice.igst_paise)],
            ["Total (Rs):", format_rupees(invoice.total_paise)],
        ]

    totals_table = Table(totals_data, colWidths=[423, 100])
    totals_table.setStyle(
        TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (0, 0), (0, -1), "RIGHT"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("LINEABOVE", (0, -1), (-1, -1), 0.5, colors.black),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ])
    )
    story.append(totals_table)
    story.append(Spacer(1, 15))

    # Footer lines
    supply_text = (
        "Intra-state supply (CGST + SGST)"
        if is_intra
        else "Inter-state supply (IGST)"
    )
    story.append(Paragraph(supply_text, footer_style))
    story.append(Spacer(1, 3))
    story.append(Paragraph("This is a computer-generated invoice.", footer_style))
    story.append(Spacer(1, 3))
    story.append(Paragraph("Dummy data for evaluation only.", footer_style))

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
