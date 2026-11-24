"""
core/reports.py - READ-ONLY report functions.

No writes. No AuditLog. Every function returns:
  {
    "title": str,
    "columns": [str],
    "rows": [list],          # raw integers (paise or base units); never floats
    "totals": list or None,
    "column_types": [str],   # "text", "money", "qty", "int"
  }

StockLedger movement → stock_summary classification:
  OPENING   / source_type "Opening"       → opening balance (subtracted before period)
  PURCHASE  / source_type "Purchase"      → receipts
  PROD_OUTPUT / source_type "ProductionRun" (positive qty) → receipts
  PROD_ISSUE  / source_type "ProductionRun" (negative qty) → issues
  DISPATCH  / source_type "Invoice"       → issues
  SALE_RETURN / source_type "CreditNote"  → receipts (reverse of dispatch)
  ADJUSTMENT                              → adjustments (signed)
"""

import csv
import io
from datetime import date as _date
from typing import Optional

from django.db.models import Q, Sum

from core.models import (
    CreditNote,
    Invoice,
    InvoiceLine,
    Item,
    Payment,
    ProductionRun,
    ProductionRunLine,
    Purchase,
    StockLedger,
)
from core.services import invoice_outstanding, is_flagged, variance_bp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _date_filter(qs, field: str, date_from, date_to):
    """Apply inclusive date range filter to a queryset."""
    if date_from is not None:
        qs = qs.filter(**{f"{field}__gte": date_from})
    if date_to is not None:
        qs = qs.filter(**{f"{field}__lte": date_to})
    return qs


def _ledger_date_filter(qs, date_from, date_to):
    """Filter StockLedger rows by created_at date (date part)."""
    if date_from is not None:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to is not None:
        qs = qs.filter(created_at__date__lte=date_to)
    return qs


def _ledger_before(item, date_from):
    """Return the balance for item in all ledger rows strictly before date_from."""
    if date_from is None:
        return 0
    total = (
        StockLedger.objects.filter(item=item, created_at__date__lt=date_from)
        .aggregate(t=Sum("quantity"))["t"]
    )
    return total or 0


def _get_stock_opening(item, date_from):
    """
    Opening balance for stock_summary.
    - No date_from: sum only the OPENING movement rows (the explicit opening stock entries).
    - With date_from: balance of ALL rows strictly before date_from (includes OPENING rows).
    """
    if date_from is None:
        total = (
            StockLedger.objects.filter(
                item=item,
                movement_type=StockLedger.MovementType.OPENING,
            ).aggregate(t=Sum("quantity"))["t"]
        )
        return total or 0
    # With a date bound, the opening is everything that happened before date_from
    total = (
        StockLedger.objects.filter(item=item, created_at__date__lt=date_from)
        .aggregate(t=Sum("quantity"))["t"]
    )
    return total or 0


# ---------------------------------------------------------------------------
# 1. Sales Register
# ---------------------------------------------------------------------------

def sales_register(date_from=None, date_to=None) -> dict:
    """
    One row per invoice within [date_from, date_to].
    Columns: number, date, customer, gstin, place_of_supply, status,
             taxable_paise, cgst_paise, sgst_paise, igst_paise, total_paise.
    Totals over non-CANCELLED invoices only.
    """
    qs = Invoice.objects.select_related("order__customer").order_by("invoice_date", "seq")
    qs = _date_filter(qs, "invoice_date", date_from, date_to)

    rows = []
    tot_taxable = tot_cgst = tot_sgst = tot_igst = tot_total = 0

    for inv in qs:
        row = [
            inv.number,
            inv.invoice_date,
            inv.customer_name,
            inv.customer_gstin,
            inv.customer_state_code,
            inv.status,
            inv.taxable_paise,
            inv.cgst_paise,
            inv.sgst_paise,
            inv.igst_paise,
            inv.total_paise,
        ]
        rows.append(row)
        if inv.status != Invoice.Status.CANCELLED:
            tot_taxable += inv.taxable_paise
            tot_cgst += inv.cgst_paise
            tot_sgst += inv.sgst_paise
            tot_igst += inv.igst_paise
            tot_total += inv.total_paise

    totals = ["", "", "", "", "", "TOTAL", tot_taxable, tot_cgst, tot_sgst, tot_igst, tot_total]

    return {
        "title": "Sales Register",
        "columns": [
            "Invoice No", "Date", "Customer", "GSTIN", "Place of Supply", "Status",
            "Taxable (Rs)", "CGST (Rs)", "SGST (Rs)", "IGST (Rs)", "Total (Rs)",
        ],
        "rows": rows,
        "totals": totals,
        "column_types": [
            "text", "text", "text", "text", "text", "text",
            "money", "money", "money", "money", "money",
        ],
    }


# ---------------------------------------------------------------------------
# 2. Stock Summary
# ---------------------------------------------------------------------------

def stock_summary(date_from=None, date_to=None) -> dict:
    """
    Per item: kind, unit, opening, receipts, issues, adjustments, closing.
    Identity: opening + receipts - issues + adjustments == closing.

    Movement classification:
      opening  = sum of ALL ledger rows before date_from (or all rows if no date_from)
                 that have movement_type OPENING
                 PLUS any rows before date_from regardless of type
                 (we use the running balance before date_from as "opening")
      receipts = PURCHASE + PROD_OUTPUT + SALE_RETURN in period
      issues   = abs(PROD_ISSUE) + abs(DISPATCH) in period (qty is negative in DB)
      adjustments = ADJUSTMENT in period (signed)
      closing  = opening + receipts - issues + adjustments
    """
    items = Item.objects.order_by("kind", "name")
    rows = []

    for item in items:
        # Opening: balance of ALL movements before date_from, or just OPENING rows if no date_from
        opening = _get_stock_opening(item, date_from)

        # Base queryset for the period
        period_qs = StockLedger.objects.filter(item=item)
        period_qs = _ledger_date_filter(period_qs, date_from, date_to)

        # Receipts: PURCHASE, PROD_OUTPUT, SALE_RETURN
        receipts = (
            period_qs.filter(
                movement_type__in=[
                    StockLedger.MovementType.PURCHASE,
                    StockLedger.MovementType.PROD_OUTPUT,
                    StockLedger.MovementType.SALE_RETURN,
                ]
            ).aggregate(t=Sum("quantity"))["t"]
            or 0
        )

        # Issues: PROD_ISSUE + DISPATCH (stored as negative; we return positive integer)
        issues_sum = (
            period_qs.filter(
                movement_type__in=[
                    StockLedger.MovementType.PROD_ISSUE,
                    StockLedger.MovementType.DISPATCH,
                ]
            ).aggregate(t=Sum("quantity"))["t"]
            or 0
        )
        issues = abs(issues_sum)

        # Adjustments: ADJUSTMENT (signed)
        adjustments = (
            period_qs.filter(
                movement_type=StockLedger.MovementType.ADJUSTMENT
            ).aggregate(t=Sum("quantity"))["t"]
            or 0
        )

        closing = opening + receipts - issues + adjustments

        rows.append([
            item.name,
            item.kind,
            item.base_unit,
            opening,
            receipts,
            issues,
            adjustments,
            closing,
        ])

    return {
        "title": "Stock Summary",
        "columns": [
            "Item", "Kind", "Unit", "Opening", "Receipts", "Issues", "Adjustments", "Closing",
        ],
        "rows": rows,
        "totals": None,
        "column_types": ["text", "text", "text", "qty", "qty", "qty", "qty", "qty"],
    }


# ---------------------------------------------------------------------------
# 3. Stock Ledger (per item)
# ---------------------------------------------------------------------------

def stock_ledger(item, date_from=None, date_to=None) -> dict:
    """
    Every movement for one item in the period.
    Columns: date, movement_type, source_type, source_id, qty_in, qty_out, running_balance.
    Running balance starts from balance of all rows before date_from.
    """
    if isinstance(item, int):
        item = Item.objects.get(pk=item)

    opening = _ledger_before(item, date_from)

    qs = StockLedger.objects.filter(item=item).order_by("created_at", "id")
    qs = _ledger_date_filter(qs, date_from, date_to)

    rows = []
    balance = opening

    for row in qs:
        qty = row.quantity
        qty_in = qty if qty > 0 else 0
        qty_out = abs(qty) if qty < 0 else 0
        balance += qty
        rows.append([
            row.created_at.date(),
            row.movement_type,
            row.source_type,
            row.source_id,
            qty_in,
            qty_out,
            balance,
        ])

    return {
        "title": f"Stock Ledger: {item.name}",
        "columns": [
            "Date", "Movement Type", "Source Type", "Source ID",
            "Qty In", "Qty Out", "Running Balance",
        ],
        "rows": rows,
        "totals": None,
        "column_types": ["text", "text", "text", "int", "qty", "qty", "qty"],
    }


# ---------------------------------------------------------------------------
# 4. Customer Sales
# ---------------------------------------------------------------------------

def customer_sales(date_from=None, date_to=None) -> dict:
    """
    Per customer (non-cancelled invoices): invoice count, taxable, tax, total.
    """
    qs = Invoice.objects.filter(
        status=Invoice.Status.ISSUED
    ).select_related("order__customer")
    qs = _date_filter(qs, "invoice_date", date_from, date_to)

    # Group by customer name (copied at invoice time)
    from collections import defaultdict
    summary = defaultdict(lambda: [0, 0, 0, 0])  # count, taxable, tax, total

    for inv in qs:
        name = inv.customer_name
        summary[name][0] += 1
        summary[name][1] += inv.taxable_paise
        summary[name][2] += inv.cgst_paise + inv.sgst_paise + inv.igst_paise
        summary[name][3] += inv.total_paise

    rows = []
    tot_count = tot_taxable = tot_tax = tot_total = 0
    for name, (count, taxable, tax, total) in sorted(summary.items()):
        rows.append([name, count, taxable, tax, total])
        tot_count += count
        tot_taxable += taxable
        tot_tax += tax
        tot_total += total

    totals = ["TOTAL", tot_count, tot_taxable, tot_tax, tot_total]

    return {
        "title": "Customer Sales",
        "columns": ["Customer", "Invoice Count", "Taxable (Rs)", "Tax (Rs)", "Total (Rs)"],
        "rows": rows,
        "totals": totals,
        "column_types": ["text", "int", "money", "money", "money"],
    }


# ---------------------------------------------------------------------------
# 5. Product Sales
# ---------------------------------------------------------------------------

def product_sales(date_from=None, date_to=None) -> dict:
    """
    Per product (non-cancelled invoices): quantity, taxable, tax, total.
    """
    qs = InvoiceLine.objects.filter(
        invoice__status=Invoice.Status.ISSUED
    ).select_related("invoice", "product")
    qs = _date_filter(qs, "invoice__invoice_date", date_from, date_to)

    from collections import defaultdict
    summary = defaultdict(lambda: [0, 0, 0, 0])  # qty, taxable, tax, total

    for line in qs:
        name = line.description
        summary[name][0] += line.quantity
        summary[name][1] += line.taxable_paise
        summary[name][2] += line.cgst_paise + line.sgst_paise + line.igst_paise
        summary[name][3] += line.taxable_paise + line.cgst_paise + line.sgst_paise + line.igst_paise

    rows = []
    tot_qty = tot_taxable = tot_tax = tot_total = 0
    for name, (qty, taxable, tax, total) in sorted(summary.items()):
        rows.append([name, qty, taxable, tax, total])
        tot_qty += qty
        tot_taxable += taxable
        tot_tax += tax
        tot_total += total

    totals = ["TOTAL", tot_qty, tot_taxable, tot_tax, tot_total]

    return {
        "title": "Product Sales",
        "columns": ["Product", "Quantity (pcs)", "Taxable (Rs)", "Tax (Rs)", "Total (Rs)"],
        "rows": rows,
        "totals": totals,
        "column_types": ["text", "int", "money", "money", "money"],
    }


# ---------------------------------------------------------------------------
# 6. GST Summary
# ---------------------------------------------------------------------------

def gst_summary(date_from=None, date_to=None) -> dict:
    """
    Grouped by supply_type and gst_rate_bp from InvoiceLines of non-cancelled invoices.
    Also provides GSTR-3B 3.1(a) totals and credit note section.
    """
    qs = InvoiceLine.objects.filter(
        invoice__status=Invoice.Status.ISSUED
    ).select_related("invoice")
    qs = _date_filter(qs, "invoice__invoice_date", date_from, date_to)

    from collections import defaultdict
    # Key: (supply_type, gst_rate_bp)
    groups = defaultdict(lambda: [0, 0, 0, 0, 0])  # taxable, cgst, sgst, igst, total_tax

    for line in qs:
        key = (line.invoice.supply_type, line.gst_rate_bp)
        tax = line.cgst_paise + line.sgst_paise + line.igst_paise
        groups[key][0] += line.taxable_paise
        groups[key][1] += line.cgst_paise
        groups[key][2] += line.sgst_paise
        groups[key][3] += line.igst_paise
        groups[key][4] += tax

    rows = []
    grand_taxable = grand_cgst = grand_sgst = grand_igst = 0
    for (stype, rate_bp), (taxable, cgst, sgst, igst, tax) in sorted(groups.items()):
        supply_label = "Intra-state" if stype == Invoice.SupplyType.INTRA else "Inter-state"
        rows.append([supply_label, rate_bp, taxable, cgst, sgst, igst, tax])
        grand_taxable += taxable
        grand_cgst += cgst
        grand_sgst += sgst
        grand_igst += igst

    # GSTR-3B 3.1(a): taxable, igst, cgst, sgst
    gstr3b = [grand_taxable, grand_igst, grand_cgst, grand_sgst]

    # Credit notes in range
    cn_qs = CreditNote.objects.all()
    cn_qs = _date_filter(cn_qs, "note_date", date_from, date_to)
    cn_count = cn_qs.count()
    cn_total = cn_qs.aggregate(t=Sum("total_paise"))["t"] or 0

    totals = ["TOTAL", "", grand_taxable, grand_cgst, grand_sgst, grand_igst,
              grand_cgst + grand_sgst + grand_igst]

    return {
        "title": "GST Summary",
        "columns": [
            "Supply Type", "GST Rate (bp)", "Taxable (Rs)", "CGST (Rs)",
            "SGST (Rs)", "IGST (Rs)", "Total Tax (Rs)",
        ],
        "rows": rows,
        "totals": totals,
        "column_types": ["text", "int", "money", "money", "money", "money", "money"],
        # Extra structured data embedded
        "gstr3b": gstr3b,                      # [taxable, igst, cgst, sgst]
        "credit_notes": {"count": cn_count, "total_paise": cn_total},
    }


# ---------------------------------------------------------------------------
# 7. Purchase Register
# ---------------------------------------------------------------------------

def purchase_register(date_from=None, date_to=None) -> dict:
    """
    Per purchase: supplier, item, quantity (base units), rate_paise_per_display_unit,
    total_value_paise, purchase_date.
    Rate = total_value_paise / (quantity in display unit).
    Display unit: g→kg (÷1000), ml→L (÷1000), pcs unchanged.
    """
    qs = Purchase.objects.select_related("supplier", "item").order_by("purchase_date", "id")
    qs = _date_filter(qs, "purchase_date", date_from, date_to)

    rows = []
    tot_amount = 0

    for p in qs:
        unit = p.item.base_unit
        qty_base = p.quantity
        # Rate per display unit in paise
        if unit == "g":
            display_qty = qty_base // 1000  # kg
            # rate = total / (qty in kg)
            rate = p.total_value_paise * 1000 // qty_base if qty_base else 0
            display_unit = "kg"
        elif unit == "ml":
            display_qty = qty_base // 1000  # L
            rate = p.total_value_paise * 1000 // qty_base if qty_base else 0
            display_unit = "L"
        else:  # pcs
            display_qty = qty_base
            rate = p.total_value_paise // qty_base if qty_base else 0
            display_unit = "pcs"

        rows.append([
            p.supplier.name,
            p.item.name,
            qty_base,           # raw base units (qty column)
            display_unit,
            rate,               # paise per display unit (money)
            p.total_value_paise,
            str(p.purchase_date),
        ])
        tot_amount += p.total_value_paise

    totals = ["", "", "", "", "", tot_amount, ""]

    return {
        "title": "Purchase Register",
        "columns": [
            "Supplier", "Item", "Quantity (base)", "Display Unit",
            "Rate (Rs/display unit)", "Amount (Rs)", "Date",
        ],
        "rows": rows,
        "totals": totals,
        "column_types": ["text", "text", "qty", "text", "money", "money", "text"],
    }


# ---------------------------------------------------------------------------
# 8. Production Report
# ---------------------------------------------------------------------------

def production_report(date_from=None, date_to=None) -> dict:
    """
    Per run, per material line: standard_qty, actual_qty, variance_bp, flagged.
    Also per run: expected yield, good packets, yield variance bp, yield flagged,
    total wastage pcs (sum of wastage_qty), material cost.
    """
    runs_qs = ProductionRun.objects.select_related("bom__product").order_by("created_at")
    runs_qs = _ledger_date_filter(runs_qs, date_from, date_to)  # filter by created_at date

    rows = []
    for run in runs_qs:
        # run summary row
        total_wastage = sum(line.wastage_qty for line in run.lines.all())
        rows.append([
            f"RUN#{run.id}",
            run.bom.product.name,
            "",   # material item
            run.expected_output,   # planned (yield)
            run.actual_output,     # actual (yield)
            run.yield_variance_bp,
            "Yes" if run.yield_flagged else "No",
            total_wastage,
            run.material_cost_paise,
        ])

        # one row per material line
        for line in run.lines.select_related("item").order_by("id"):
            rows.append([
                f"RUN#{run.id}",
                "",
                line.item.name,
                line.standard_qty,
                line.actual_qty,
                line.variance_bp,
                "Yes" if line.flagged else "No",
                line.wastage_qty,
                line.cost_paise,
            ])

    return {
        "title": "Production Report",
        "columns": [
            "Run", "Product", "Material",
            "Standard Qty", "Actual Qty", "Variance (bp)", "Flagged",
            "Wastage", "Cost (Rs)",
        ],
        "rows": rows,
        "totals": None,
        "column_types": [
            "text", "text", "text",
            "qty", "qty", "int", "text",
            "qty", "money",
        ],
    }


# ---------------------------------------------------------------------------
# 9. Payments Report
# ---------------------------------------------------------------------------

def payments_report(date_from=None, date_to=None) -> dict:
    """
    Per payment: date, customer (from invoice), invoice number, amount.
    Total collected.
    """
    qs = Payment.objects.select_related(
        "invoice__order__customer"
    ).order_by("payment_date", "id")
    qs = _date_filter(qs, "payment_date", date_from, date_to)

    rows = []
    tot_amount = 0
    for pay in qs:
        rows.append([
            str(pay.payment_date),
            pay.invoice.customer_name,
            pay.invoice.number,
            pay.amount_paise,
        ])
        tot_amount += pay.amount_paise

    totals = ["", "", "TOTAL", tot_amount]

    return {
        "title": "Payments Report",
        "columns": ["Date", "Customer", "Invoice No", "Amount (Rs)"],
        "rows": rows,
        "totals": totals,
        "column_types": ["text", "text", "text", "money"],
    }


# ---------------------------------------------------------------------------
# 10. Receivables Ageing
# ---------------------------------------------------------------------------

def receivables_ageing(as_of=None) -> dict:
    """
    Per customer: outstanding split into buckets 0-30, 31-60, 61-90, 90+ days.
    Uses invoice_date to as_of for age. Skips cancelled and fully-paid invoices.
    """
    if as_of is None:
        from django.utils import timezone
        as_of = timezone.localdate()

    # Only ISSUED invoices (not cancelled)
    qs = Invoice.objects.filter(
        status=Invoice.Status.ISSUED
    ).select_related("order__customer").order_by("order__customer__name", "invoice_date")

    from collections import defaultdict
    # customer_name -> [0-30, 31-60, 61-90, 90+]
    buckets = defaultdict(lambda: [0, 0, 0, 0])

    for inv in qs:
        outstanding = invoice_outstanding(inv)
        if outstanding <= 0:
            continue
        days = (as_of - inv.invoice_date).days
        if days <= 30:
            idx = 0
        elif days <= 60:
            idx = 1
        elif days <= 90:
            idx = 2
        else:
            idx = 3
        buckets[inv.customer_name][idx] += outstanding

    rows = []
    tot = [0, 0, 0, 0]
    for name, bkts in sorted(buckets.items()):
        rows.append([name] + bkts)
        for i in range(4):
            tot[i] += bkts[i]

    totals = ["TOTAL"] + tot

    return {
        "title": f"Receivables Ageing as of {as_of}",
        "columns": ["Customer", "0-30 days (Rs)", "31-60 days (Rs)", "61-90 days (Rs)", "90+ days (Rs)"],
        "rows": rows,
        "totals": totals,
        "column_types": ["text", "money", "money", "money", "money"],
    }


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def _display_unit(base_unit: str) -> str:
    """Map internal base unit to display unit: g -> kg, ml -> L, pcs -> pcs."""
    if base_unit == "g":
        return "kg"
    elif base_unit == "ml":
        return "L"
    return base_unit


def _paise_to_csv(paise: int) -> str:
    """
    Convert integer paise to plain rupee string with 2 decimal places.
    No commas, no symbol. Pure integer arithmetic.
    e.g. 787500 -> "7875.00", 18750 -> "187.50"
    """
    rupees, cents = divmod(abs(paise), 100)
    sign = "-" if paise < 0 else ""
    return f"{sign}{rupees}.{cents:02d}"


def _qty_to_csv(qty: int, unit: str) -> str:
    """
    Convert base-unit integer quantity to display string.
    g -> kg (÷1000, 3 decimals), ml -> L (÷1000, 3 decimals), pcs -> unchanged integer.
    """
    if unit == "g":
        whole, frac = divmod(abs(qty), 1000)
        sign = "-" if qty < 0 else ""
        return f"{sign}{whole}.{frac:03d}"
    elif unit == "ml":
        whole, frac = divmod(abs(qty), 1000)
        sign = "-" if qty < 0 else ""
        return f"{sign}{whole}.{frac:03d}"
    else:
        return str(qty)


def to_csv(report: dict) -> str:
    """
    Convert a report dict to a CSV string.
    - money columns: plain rupee strings with 2 decimals, no commas (e.g. 7875.00)
    - qty columns: converted to display units if Unit column exists, otherwise written as-is
    - Unit column: shows DISPLAY unit ('kg', 'L', 'pcs') when quantities are converted
    - int and text columns: written as-is.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)

    col_types = report["column_types"]
    columns = report["columns"]

    writer.writerow(columns)

    has_unit_col = "Unit" in columns
    unit_col_idx = columns.index("Unit") if has_unit_col else None

    def format_row(row):
        base_unit = row[unit_col_idx] if (has_unit_col and unit_col_idx < len(row)) else None
        out = []
        for i, (val, ctype) in enumerate(zip(row, col_types)):
            if i == unit_col_idx and isinstance(val, str):
                out.append(_display_unit(val))
            elif val is None:
                out.append("")
            elif ctype == "money" and isinstance(val, int):
                out.append(_paise_to_csv(val))
            elif ctype == "qty" and isinstance(val, int):
                if base_unit:
                    out.append(_qty_to_csv(val, base_unit))
                else:
                    out.append(str(val))
            else:
                out.append(str(val))
        return out

    for row in report["rows"]:
        writer.writerow(format_row(row))

    if report.get("totals") is not None:
        writer.writerow(format_row(report["totals"]))

    return buf.getvalue()


def to_csv_stock_summary(report: dict) -> str:
    """
    Specialized CSV for stock_summary: converts qty columns to display units per row,
    reading the 'Unit' column (index 2) to decide the conversion.
    The Unit column shows the DISPLAY unit: 'kg', 'L', or 'pcs'.
    """
    return to_csv(report)
