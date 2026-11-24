"""
core/management/commands/export_reports.py

Writes one CSV per report to --out directory (default: docs/sample_reports).
Uses utf-8-sig encoding so Excel opens it without BOM issues.

Usage:
  python manage.py export_reports
  python manage.py export_reports --from 2026-04-01 --to 2027-03-31
  python manage.py export_reports --out /path/to/output
"""

import os
from datetime import date

from django.core.management.base import BaseCommand

from core.models import Item
from core.reports import (
    customer_sales,
    gst_summary,
    payments_report,
    production_report,
    purchase_register,
    receivables_ageing,
    sales_register,
    stock_ledger,
    stock_summary,
    product_sales,
    to_csv,
    to_csv_stock_summary,
)


class Command(BaseCommand):
    help = "Export all reports as CSV files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--from",
            dest="date_from",
            default=None,
            help="Start date inclusive (YYYY-MM-DD). Omit for all dates.",
        )
        parser.add_argument(
            "--to",
            dest="date_to",
            default=None,
            help="End date inclusive (YYYY-MM-DD). Omit for all dates.",
        )
        parser.add_argument(
            "--out",
            dest="out_dir",
            default="docs/sample_reports",
            help="Output directory for CSV files (default: docs/sample_reports).",
        )

    def handle(self, *args, **options):
        date_from = None
        date_to = None

        if options["date_from"]:
            date_from = date.fromisoformat(options["date_from"])
        if options["date_to"]:
            date_to = date.fromisoformat(options["date_to"])

        out_dir = options["out_dir"]
        os.makedirs(out_dir, exist_ok=True)

        def write_csv(filename, csv_text):
            path = os.path.join(out_dir, filename)
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                f.write(csv_text)
            self.stdout.write(self.style.SUCCESS(f"  {path}"))
            return path

        self.stdout.write("Exporting reports...")

        # 1. Sales register
        write_csv("sales_register.csv", to_csv(sales_register(date_from, date_to)))

        # 2. Stock summary (with display-unit conversion)
        write_csv("stock_summary.csv", to_csv_stock_summary(stock_summary(date_from, date_to)))

        # 3. Stock ledger - one file per item
        for item in Item.objects.order_by("name"):
            safe_name = item.name.replace(" ", "_").replace("/", "-")
            write_csv(
                f"stock_ledger_{safe_name}.csv",
                to_csv(stock_ledger(item, date_from, date_to)),
            )

        # 4. Customer sales
        write_csv("customer_sales.csv", to_csv(customer_sales(date_from, date_to)))

        # 5. Product sales
        write_csv("product_sales.csv", to_csv(product_sales(date_from, date_to)))

        # 6. GST summary
        write_csv("gst_summary.csv", to_csv(gst_summary(date_from, date_to)))

        # 7. Purchase register
        write_csv("purchase_register.csv", to_csv(purchase_register(date_from, date_to)))

        # 8. Production report
        write_csv("production_report.csv", to_csv(production_report(date_from, date_to)))

        # 9. Payments report
        write_csv("payments_report.csv", to_csv(payments_report(date_from, date_to)))

        # 10. Receivables ageing (as_of = date_to if given, else today)
        write_csv("receivables_ageing.csv", to_csv(receivables_ageing(as_of=date_to)))

        self.stdout.write(self.style.SUCCESS("Done."))
