import os
from datetime import date
from decimal import Decimal
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
import reportlab.rl_config

from core.models import (
    Item,
    StockLedger,
    Supplier,
    Purchase,
    BOM,
    BOMLine,
    ProductionRun,
    Customer,
    ProductPrice,
    Order,
    OrderLine,
    Invoice,
    InvoiceLine,
    Payment,
    CreditNote,
    AuditLog,
    Expense,
)
from core.services import (
    get_balance,
    get_value,
    to_base,
    value_for_issue,
    record_purchase,
    variance_bp,
    is_flagged,
    record_production_run,
    order_total_paise,
    place_order,
    stock_report,
    update_order_line,
    approve_order,
    reject_order,
    financial_year,
    gst_for_line,
    create_invoice,
    record_dispatch,
    invoice_outstanding,
    customer_outstanding,
    record_payment,
    cancel_invoice,
    log_audit,
    per_unit_paise,
    record_expense,
    run_costing,
)
from core.reports import (
    sales_register,
    stock_summary,
    stock_ledger,
    customer_sales,
    product_sales,
    gst_summary,
    purchase_register,
    production_report,
    payments_report,
    receivables_ageing,
    to_csv,
    to_csv_stock_summary,
)
from core.pdf import (
    format_rupees,
    format_rate,
    build_invoice_pdf,
)


class StockFoundationTests(TestCase):
    def test_seed_benchmark_balances(self):
        call_command("seed_benchmark")

        flour = Item.objects.get(name="Flour")
        oil = Item.objects.get(name="Cooking oil")
        spice = Item.objects.get(name="Salt and spice mix")
        pouch = Item.objects.get(name="200 g pouch")
        carton = Item.objects.get(name="Carton")
        murukku = Item.objects.get(name="Murukku 200 g")

        self.assertEqual(get_balance(flour), 100000)
        self.assertEqual(get_balance(oil), 30000)
        self.assertEqual(get_balance(spice), 5000)
        self.assertEqual(get_balance(pouch), 500)
        self.assertEqual(get_balance(carton), 30)
        self.assertEqual(get_balance(murukku), 50)

    def test_seed_benchmark_idempotent(self):
        # Running the seed twice does not change balances or duplicate rows
        call_command("seed_benchmark")
        call_command("seed_benchmark")

        flour = Item.objects.get(name="Flour")
        oil = Item.objects.get(name="Cooking oil")
        spice = Item.objects.get(name="Salt and spice mix")
        pouch = Item.objects.get(name="200 g pouch")
        carton = Item.objects.get(name="Carton")
        murukku = Item.objects.get(name="Murukku 200 g")

        self.assertEqual(get_balance(flour), 100000)
        self.assertEqual(get_balance(oil), 30000)
        self.assertEqual(get_balance(spice), 5000)
        self.assertEqual(get_balance(pouch), 500)
        self.assertEqual(get_balance(carton), 30)
        self.assertEqual(get_balance(murukku), 50)

        # Verify only 6 ledger entries created
        self.assertEqual(StockLedger.objects.count(), 6)

        # Verify BOM and lines are not duplicated
        self.assertEqual(BOM.objects.count(), 1)
        self.assertEqual(BOMLine.objects.count(), 5)

        # Verify Customers and ProductPrices are not duplicated
        self.assertEqual(Customer.objects.count(), 2)
        self.assertEqual(ProductPrice.objects.count(), 1)

    def test_to_base_valid(self):
        self.assertEqual(to_base("40", "kg"), 40000)
        self.assertEqual(to_base("10.5", "l"), 10500)
        self.assertEqual(to_base("1.2", "kg"), 1200)

        # Decimal input and 1:1 conversions
        self.assertEqual(to_base(Decimal("40"), "kg"), 40000)
        self.assertEqual(to_base("250", "ml"), 250)
        self.assertEqual(to_base("500", "g"), 500)
        self.assertEqual(to_base("10", "pcs"), 10)

    def test_to_base_invalid(self):
        # Fractional base unit is not allowed
        with self.assertRaises(ValueError):
            to_base("0.0005", "kg")

        # Unknown unit
        with self.assertRaises(ValueError):
            to_base("10", "unknown")

    def test_stock_ledger_append_only(self):
        item = Item.objects.create(
            name="Sample Item",
            kind=Item.Kind.RAW,
            base_unit=Item.BaseUnit.G,
        )
        row = StockLedger.objects.create(
            item=item,
            movement_type=StockLedger.MovementType.OPENING,
            quantity=100,
            value_paise=5000,
            source_type="Opening",
        )

        # Editing a saved row must raise an error
        row.quantity = 200
        with self.assertRaises(RuntimeError):
            row.save()

        # Deleting a saved row must raise an error
        with self.assertRaises(RuntimeError):
            row.delete()


class PurchaseAndValuationTests(TestCase):
    def setUp(self):
        call_command("seed_benchmark")
        self.supplier = Supplier.objects.create(
            name="Sri Murugan Traders", gstin="33ABCDE1234F1Z5"
        )
        self.flour = Item.objects.get(name="Flour")
        self.oil = Item.objects.get(name="Cooking oil")
        self.murukku = Item.objects.get(name="Murukku 200 g")
        self.test_date = date(2026, 11, 24)

    def test_record_purchase_and_valuation(self):
        # record_purchase(flour, "60", "kg", "45", supplier, date) makes the flour balance 160000
        # and get_value(flour) 690000; the Purchase total_value_paise is 270000
        purchase = record_purchase(
            self.flour, "60", "kg", "45", self.supplier, self.test_date, invoice_no="INV-101"
        )
        self.assertEqual(get_balance(self.flour), 160000)
        self.assertEqual(get_value(self.flour), 690000)
        self.assertEqual(purchase.total_value_paise, 270000)

        # the new ledger row has movement_type PURCHASE, source_type "Purchase" and source_id equal to the purchase id
        ledger_row = StockLedger.objects.get(source_type="Purchase", source_id=purchase.id)
        self.assertEqual(ledger_row.movement_type, StockLedger.MovementType.PURCHASE)
        self.assertEqual(ledger_row.source_type, "Purchase")
        self.assertEqual(ledger_row.source_id, purchase.id)
        self.assertEqual(ledger_row.quantity, 60000)
        self.assertEqual(ledger_row.value_paise, 270000)

        # value_for_issue(flour, 40000) == 172500 (after the purchase above)
        self.assertEqual(value_for_issue(self.flour, 40000), 172500)

        # value_for_issue raises ValueError when asked for more than the balance
        with self.assertRaises(ValueError):
            value_for_issue(self.flour, 160001)

    def test_record_purchase_finished_good_raises(self):
        # record_purchase for the Murukku 200 g item (FINISHED) raises ValueError
        with self.assertRaises(ValueError):
            record_purchase(
                self.murukku, "10", "pcs", "20", self.supplier, self.test_date
            )

    def test_record_purchase_mismatched_unit_raises(self):
        # record_purchase for flour with unit "l" raises ValueError
        with self.assertRaises(ValueError):
            record_purchase(
                self.flour, "10", "l", "45", self.supplier, self.test_date
            )

    def test_record_purchase_rounding(self):
        # rounding: record_purchase(oil, "1", "l", "140.555", ...) gives total_value_paise 14056
        purchase = record_purchase(
            self.oil, "1", "l", "140.555", self.supplier, self.test_date
        )
        self.assertEqual(purchase.total_value_paise, 14056)


class ProductionRunTests(TestCase):
    def setUp(self):
        call_command("seed_benchmark")
        self.supplier = Supplier.objects.create(name="Test Supplier", gstin="33AABBCCDDEE1Z5")
        self.flour = Item.objects.get(name="Flour")
        self.oil = Item.objects.get(name="Cooking oil")
        self.spice = Item.objects.get(name="Salt and spice mix")
        self.pouch = Item.objects.get(name="200 g pouch")
        self.carton = Item.objects.get(name="Carton")
        self.murukku = Item.objects.get(name="Murukku 200 g")
        self.bom = BOM.objects.get(product=self.murukku)

        # Record purchase of 60 kg flour at Rs 45
        record_purchase(
            self.flour,
            "60",
            "kg",
            "45",
            self.supplier,
            date(2026, 11, 24),
            invoice_no="INV-SETUP",
        )

    def test_production_run_success(self):
        # Run record_production_run with actual_output 188
        lines_input = {
            self.flour: {"qty": 40000},
            self.oil: {"qty": 10500},
            self.spice: {"qty": 1200},
            self.pouch: {"qty": 194, "wastage": 6},
            self.carton: {"qty": 9},
        }

        run = record_production_run(self.bom, 188, lines_input)

        # Assert balances: flour 120000, oil 19500, spice 3800, pouch 306, carton 21, Murukku 200 g 238
        self.assertEqual(get_balance(self.flour), 120000)
        self.assertEqual(get_balance(self.oil), 19500)
        self.assertEqual(get_balance(self.spice), 3800)
        self.assertEqual(get_balance(self.pouch), 306)
        self.assertEqual(get_balance(self.carton), 21)
        self.assertEqual(get_balance(self.murukku), 238)

        # Assert expected_output 200, yield_variance_bp -600, yield_flagged True
        self.assertEqual(run.expected_output, 200)
        self.assertEqual(run.yield_variance_bp, -600)
        self.assertTrue(run.yield_flagged)

        # Line variance_bp & flagged:
        # flour 0 (not flagged), oil 500 (flagged), spice 0 (not flagged),
        # pouch -300 (NOT flagged because 300 is not greater than 300), carton -1000 (flagged)
        line_flour = run.lines.get(item=self.flour)
        line_oil = run.lines.get(item=self.oil)
        line_spice = run.lines.get(item=self.spice)
        line_pouch = run.lines.get(item=self.pouch)
        line_carton = run.lines.get(item=self.carton)

        self.assertEqual(line_flour.variance_bp, 0)
        self.assertFalse(line_flour.flagged)

        self.assertEqual(line_oil.variance_bp, 500)
        self.assertTrue(line_oil.flagged)

        self.assertEqual(line_spice.variance_bp, 0)
        self.assertFalse(line_spice.flagged)

        self.assertEqual(line_pouch.variance_bp, -300)
        self.assertFalse(line_pouch.flagged)

        self.assertEqual(line_carton.variance_bp, -1000)
        self.assertTrue(line_carton.flagged)

        # Assert material_cost_paise 405500; per-line cost_paise:
        # flour 172500, oil 147000, spice 24000, pouch 48500, carton 13500
        self.assertEqual(run.material_cost_paise, 405500)
        self.assertEqual(line_flour.cost_paise, 172500)
        self.assertEqual(line_oil.cost_paise, 147000)
        self.assertEqual(line_spice.cost_paise, 24000)
        self.assertEqual(line_pouch.cost_paise, 48500)
        self.assertEqual(line_carton.cost_paise, 13500)

        # Assert the pouch line wastage_qty is 6
        self.assertEqual(line_pouch.wastage_qty, 6)

        # Assert every new ledger row has source_type "ProductionRun" and source_id equal to the run id
        prod_ledger_rows = StockLedger.objects.filter(source_type="ProductionRun")
        self.assertEqual(prod_ledger_rows.count(), 6)  # 5 issues + 1 output
        for row in prod_ledger_rows:
            self.assertEqual(row.source_id, run.id)

        # Assert the Murukku output ledger row has value_paise 405500
        output_row = StockLedger.objects.get(
            source_type="ProductionRun",
            source_id=run.id,
            movement_type=StockLedger.MovementType.PROD_OUTPUT,
        )
        self.assertEqual(output_row.item, self.murukku)
        self.assertEqual(output_row.quantity, 188)
        self.assertEqual(output_row.value_paise, 405500)

    def test_production_run_higher_tolerance(self):
        # with tolerance_percent=10, nothing is flagged (including the yield and the oil)
        lines_input = {
            self.flour: {"qty": 40000},
            self.oil: {"qty": 10500},
            self.spice: {"qty": 1200},
            self.pouch: {"qty": 194, "wastage": 6},
            self.carton: {"qty": 9},
        }

        run = record_production_run(self.bom, 188, lines_input, tolerance_percent=10)
        self.assertFalse(run.yield_flagged)
        for line in run.lines.all():
            self.assertFalse(line.flagged)

    def test_production_run_rollback_on_short_stock(self):
        # rollback: a run whose oil qty is 99999999 raises ValueError, and afterwards the flour
        # balance is unchanged, no ProductionRun was created and no new ledger rows exist
        initial_flour_balance = get_balance(self.flour)
        initial_ledger_count = StockLedger.objects.count()
        initial_runs_count = ProductionRun.objects.count()

        lines_input = {
            self.flour: {"qty": 40000},
            self.oil: {"qty": 99999999},
            self.spice: {"qty": 1200},
            self.pouch: {"qty": 194},
            self.carton: {"qty": 9},
        }

        with self.assertRaises(ValueError):
            record_production_run(self.bom, 188, lines_input)

        self.assertEqual(get_balance(self.flour), initial_flour_balance)
        self.assertEqual(StockLedger.objects.count(), initial_ledger_count)
        self.assertEqual(ProductionRun.objects.count(), initial_runs_count)

    def test_production_run_missing_bom_item_raises(self):
        # a run with a missing BOM item in lines raises ValueError
        lines_missing = {
            self.flour: {"qty": 40000},
            self.oil: {"qty": 10000},
            # missing spice, pouch, carton
        }
        with self.assertRaises(ValueError):
            record_production_run(self.bom, 188, lines_missing)


class OrderApprovalTests(TestCase):
    def setUp(self):
        # Helper: runs seed, buys 60 kg flour at Rs 45, and runs benchmark production run
        call_command("seed_benchmark")
        self.supplier = Supplier.objects.create(name="Test Supplier", gstin="33AABBCCDDEE1Z5")
        self.flour = Item.objects.get(name="Flour")
        self.oil = Item.objects.get(name="Cooking oil")
        self.spice = Item.objects.get(name="Salt and spice mix")
        self.pouch = Item.objects.get(name="200 g pouch")
        self.carton = Item.objects.get(name="Carton")
        self.murukku = Item.objects.get(name="Murukku 200 g")
        self.bom = BOM.objects.get(product=self.murukku)

        # Buy 60 kg flour at Rs 45
        record_purchase(
            self.flour,
            "60",
            "kg",
            "45",
            self.supplier,
            date(2026, 11, 24),
            invoice_no="INV-SETUP",
        )

        # Run benchmark production run (actual_output 188)
        record_production_run(
            self.bom,
            188,
            {
                self.flour: {"qty": 40000},
                self.oil: {"qty": 10500},
                self.spice: {"qty": 1200},
                self.pouch: {"qty": 194, "wastage": 6},
                self.carton: {"qty": 9},
            },
        )
        # Finished stock of Murukku is now 50 + 188 = 238
        self.assertEqual(get_balance(self.murukku), 238)

        # Users
        User = get_user_model()
        self.salesman_a = User.objects.create_user(username="salesman_a", password="password")
        self.salesman_b = User.objects.create_user(username="salesman_b", password="password")
        self.admin1 = User.objects.create_user(username="admin1", password="password", is_staff=True)

        self.retailer_a = Customer.objects.get(name="Retailer A")
        self.distributor_b = Customer.objects.get(name="Distributor B")

    def test_order_placement_approval_dispatch_and_rejection_flow(self):
        # 1. place_order(Retailer A, salesman_a, [(murukku, 150)]):
        #    status SUBMITTED, line unit_price_paise 5000, order_total_paise 750000
        order1 = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 150)])
        self.assertEqual(order1.status, Order.Status.SUBMITTED)
        line1 = order1.lines.get(product=self.murukku)
        self.assertEqual(line1.unit_price_paise, 5000)
        self.assertEqual(order_total_paise(order1), 750000)

        # 2. stock_report shows available 238, shortage 0
        rep1 = stock_report(order1)
        self.assertEqual(len(rep1), 1)
        self.assertEqual(rep1[0]["product"], self.murukku)
        self.assertEqual(rep1[0]["ordered"], 150)
        self.assertEqual(rep1[0]["available"], 238)
        self.assertEqual(rep1[0]["shortage"], 0)

        # 3. approve_order works: status APPROVED, decided_by is admin1, decided_at is set
        approve_order(order1, user=self.admin1)
        self.assertEqual(order1.status, Order.Status.APPROVED)
        self.assertEqual(order1.decided_by, self.admin1)
        self.assertIsNotNone(order1.decided_at)

        # 4. simulate dispatch:
        #    StockLedger: item Murukku 200 g, movement_type DISPATCH, quantity -150,
        #    value_paise -value_for_issue(murukku, 150), source_type "Order"
        #    finished stock is now 88
        issue_cost = value_for_issue(self.murukku, 150)
        StockLedger.objects.create(
            item=self.murukku,
            movement_type=StockLedger.MovementType.DISPATCH,
            quantity=-150,
            value_paise=-issue_cost,
            source_type="Order",
            source_id=order1.id,
            note=f"Dispatch for Order #{order1.id}",
        )
        self.assertEqual(get_balance(self.murukku), 88)

        # 5. place_order(Distributor B, salesman_b, [(murukku, 120)]):
        #    stock_report shows available 88, shortage 32
        order2 = place_order(self.distributor_b, self.salesman_b, [(self.murukku, 120)])
        rep2 = stock_report(order2)
        self.assertEqual(len(rep2), 1)
        self.assertEqual(rep2[0]["available"], 88)
        self.assertEqual(rep2[0]["shortage"], 32)

        # 6. approve_order on that order raises ValueError with "32" in message, still SUBMITTED
        with self.assertRaises(ValueError) as ctx:
            approve_order(order2, user=self.admin1)
        self.assertIn("32", str(ctx.exception))
        order2.refresh_from_db()
        self.assertEqual(order2.status, Order.Status.SUBMITTED)

        # 7. update_order_line to quantity 88, then approve_order succeeds
        update_order_line(order2, self.murukku, 88, user=self.admin1)
        line2 = order2.lines.get(product=self.murukku)
        self.assertEqual(line2.quantity, 88)
        approve_order(order2, user=self.admin1)
        self.assertEqual(order2.status, Order.Status.APPROVED)

        # 8. reject_order sets REJECTED and stores reason; rejecting with empty reason raises ValueError
        order3 = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 10)])
        with self.assertRaises(ValueError):
            reject_order(order3, user=self.admin1, reason="")
        with self.assertRaises(ValueError):
            reject_order(order3, user=self.admin1, reason="   ")

        reject_order(order3, user=self.admin1, reason="Credit limit exceeded")
        self.assertEqual(order3.status, Order.Status.REJECTED)
        self.assertEqual(order3.reject_reason, "Credit limit exceeded")
        self.assertEqual(order3.decided_by, self.admin1)
        self.assertIsNotNone(order3.decided_at)

        # 9. update_order_line on an APPROVED order raises ValueError
        with self.assertRaises(ValueError):
            update_order_line(order1, self.murukku, 100, user=self.admin1)

    def test_order_placement_validation_errors(self):
        # place_order with quantity 0 raises ValueError
        with self.assertRaises(ValueError):
            place_order(self.retailer_a, self.salesman_a, [(self.murukku, 0)])

        # place_order with a RAW item raises ValueError
        with self.assertRaises(ValueError):
            place_order(self.retailer_a, self.salesman_a, [(self.flour, 10)])

        # an empty lines list raises ValueError
        with self.assertRaises(ValueError):
            place_order(self.retailer_a, self.salesman_a, [])


class InvoiceDispatchPaymentTests(TestCase):
    def setUp(self):
        # Setup helper: seed, buy 60 kg flour at Rs 45, benchmark production run (stock Murukku = 238)
        call_command("seed_benchmark")
        self.supplier = Supplier.objects.create(name="Test Supplier", gstin="33AABBCCDDEE1Z5")
        self.flour = Item.objects.get(name="Flour")
        self.oil = Item.objects.get(name="Cooking oil")
        self.spice = Item.objects.get(name="Salt and spice mix")
        self.pouch = Item.objects.get(name="200 g pouch")
        self.carton = Item.objects.get(name="Carton")
        self.murukku = Item.objects.get(name="Murukku 200 g")
        self.bom = BOM.objects.get(product=self.murukku)

        # Buy 60 kg flour at Rs 45
        record_purchase(
            self.flour,
            "60",
            "kg",
            "45",
            self.supplier,
            date(2026, 11, 24),
            invoice_no="INV-SETUP",
        )

        # Run benchmark production run (actual_output 188)
        record_production_run(
            self.bom,
            188,
            {
                self.flour: {"qty": 40000},
                self.oil: {"qty": 10500},
                self.spice: {"qty": 1200},
                self.pouch: {"qty": 194, "wastage": 6},
                self.carton: {"qty": 9},
            },
        )
        self.assertEqual(get_balance(self.murukku), 238)

        User = get_user_model()
        self.salesman_a = User.objects.create_user(username="salesman_a", password="password")
        self.salesman_b = User.objects.create_user(username="salesman_b", password="password")
        self.admin1 = User.objects.create_user(username="admin1", password="password", is_staff=True)

        self.retailer_a = Customer.objects.get(name="Retailer A")
        self.distributor_b = Customer.objects.get(name="Distributor B")

    def test_financial_year(self):
        # financial_year: date(2027,3,31) -> "2026-27"; date(2027,4,1) -> "2027-28"; date(2026,4,1) -> "2026-27"
        self.assertEqual(financial_year(date(2027, 3, 31)), "2026-27")
        self.assertEqual(financial_year(date(2027, 4, 1)), "2027-28")
        self.assertEqual(financial_year(date(2026, 4, 1)), "2026-27")

    def test_retailer_a_invoicing_dispatch_and_payments(self):
        # Retailer A: place_order 150, approve_order, create_invoice(order, admin1, invoice_date=date(2026,11,24))
        order = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 150)])
        approve_order(order, self.admin1)
        inv = create_invoice(order, self.admin1, invoice_date=date(2026, 11, 24))

        # number "INV/2026-27/0001", supply_type INTRA, taxable_paise 750000, cgst_paise 18750,
        # sgst_paise 18750, igst_paise 0, total_paise 787500, order.status INVOICED,
        # the line hsn_code "2106", customer_gstin copied from the customer
        self.assertEqual(inv.number, "INV/2026-27/0001")
        self.assertEqual(inv.supply_type, Invoice.SupplyType.INTRA)
        self.assertEqual(inv.taxable_paise, 750000)
        self.assertEqual(inv.cgst_paise, 18750)
        self.assertEqual(inv.sgst_paise, 18750)
        self.assertEqual(inv.igst_paise, 0)
        self.assertEqual(inv.total_paise, 787500)
        self.assertEqual(inv.customer_gstin, self.retailer_a.gstin)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.INVOICED)

        line = inv.lines.first()
        self.assertEqual(line.hsn_code, "2106")

        # the invoice totals equal the sum of its lines
        self.assertEqual(inv.taxable_paise, sum(l.taxable_paise for l in inv.lines.all()))
        self.assertEqual(inv.cgst_paise, sum(l.cgst_paise for l in inv.lines.all()))
        self.assertEqual(inv.sgst_paise, sum(l.sgst_paise for l in inv.lines.all()))
        self.assertEqual(inv.igst_paise, sum(l.igst_paise for l in inv.lines.all()))
        self.assertEqual(inv.total_paise, sum(l.taxable_paise + l.cgst_paise + l.sgst_paise + l.igst_paise for l in inv.lines.all()))

        # record_dispatch(invoice, admin1): one StockLedger row with movement_type DISPATCH,
        # quantity -150, value_paise -255567, source_type "Invoice", source_id equal to the invoice id;
        # Murukku 200 g balance is 88; order.status DISPATCHED
        record_dispatch(inv, self.admin1)

        dispatch_row = StockLedger.objects.get(source_type="Invoice", source_id=inv.id)
        self.assertEqual(dispatch_row.movement_type, StockLedger.MovementType.DISPATCH)
        self.assertEqual(dispatch_row.quantity, -150)
        self.assertEqual(dispatch_row.value_paise, -255567)
        self.assertEqual(get_balance(self.murukku), 88)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DISPATCHED)

        # record_dispatch a second time raises ValueError and the balance stays 88
        with self.assertRaises(ValueError):
            record_dispatch(inv, self.admin1)
        self.assertEqual(get_balance(self.murukku), 88)

        # payments: record_payment(invoice A, 400000, any date, "UPI") works;
        # invoice_outstanding is 387500; customer_outstanding(Retailer A) is 387500;
        # a payment of 387501 raises ValueError; a payment of 0 raises ValueError
        pay = record_payment(inv, 400000, date(2026, 11, 25), Payment.Mode.UPI, user=self.admin1)
        self.assertIsNotNone(pay.id)
        self.assertEqual(invoice_outstanding(inv), 387500)
        self.assertEqual(customer_outstanding(self.retailer_a), 387500)

        with self.assertRaises(ValueError):
            record_payment(inv, 387501, date(2026, 11, 26), Payment.Mode.BANK, user=self.admin1)

        with self.assertRaises(ValueError):
            record_payment(inv, 0, date(2026, 11, 26), Payment.Mode.CASH, user=self.admin1)

    def test_distributor_b_interstate_invoice(self):
        # Distributor B: place_order 120, approve_order raises ValueError containing "32";
        # update_order_line to 88; approve_order;
        # create_invoice(..., invoice_date=date(2026,11,24)):
        # number "INV/2026-27/0002", supply_type INTER, taxable_paise 440000,
        # igst_paise 22000, cgst_paise 0, sgst_paise 0, total_paise 462000
        order_a = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 150)])
        approve_order(order_a, self.admin1)
        create_invoice(order_a, self.admin1, invoice_date=date(2026, 11, 24))
        record_dispatch(order_a.invoice, self.admin1)  # Murukku stock is now 88

        order_b = place_order(self.distributor_b, self.salesman_b, [(self.murukku, 120)])
        with self.assertRaises(ValueError) as ctx:
            approve_order(order_b, self.admin1)
        self.assertIn("32", str(ctx.exception))

        update_order_line(order_b, self.murukku, 88, user=self.admin1)
        approve_order(order_b, self.admin1)
        inv_b = create_invoice(order_b, self.admin1, invoice_date=date(2026, 11, 24))

        self.assertEqual(inv_b.number, "INV/2026-27/0002")
        self.assertEqual(inv_b.supply_type, Invoice.SupplyType.INTER)
        self.assertEqual(inv_b.taxable_paise, 440000)
        self.assertEqual(inv_b.igst_paise, 22000)
        self.assertEqual(inv_b.cgst_paise, 0)
        self.assertEqual(inv_b.sgst_paise, 0)
        self.assertEqual(inv_b.total_paise, 462000)

    def test_create_invoice_validation_errors(self):
        # create_invoice on a SUBMITTED order raises ValueError;
        # create_invoice twice on the same order raises ValueError
        order = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 10)])
        with self.assertRaises(ValueError):
            create_invoice(order, self.admin1)

        approve_order(order, self.admin1)
        create_invoice(order, self.admin1)

        with self.assertRaises(ValueError):
            create_invoice(order, self.admin1)

    def test_competing_orders_dispatch(self):
        # competing orders: two APPROVED orders of 150 packets each (stock 238 at approval time),
        # both invoiced; dispatching the first works (stock 88); dispatching the second raises ValueError,
        # the second order stays INVOICED, stock stays 88 and no new ledger row exists
        order1 = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 150)])
        approve_order(order1, self.admin1)

        order2 = place_order(self.distributor_b, self.salesman_b, [(self.murukku, 150)])
        approve_order(order2, self.admin1)

        inv1 = create_invoice(order1, self.admin1, invoice_date=date(2026, 11, 24))
        inv2 = create_invoice(order2, self.admin1, invoice_date=date(2026, 11, 24))

        record_dispatch(inv1, self.admin1)
        self.assertEqual(get_balance(self.murukku), 88)

        ledger_count = StockLedger.objects.count()
        with self.assertRaises(ValueError):
            record_dispatch(inv2, self.admin1)

        order2.refresh_from_db()
        self.assertEqual(order2.status, Order.Status.INVOICED)
        self.assertEqual(get_balance(self.murukku), 88)
        self.assertEqual(StockLedger.objects.count(), ledger_count)

    def test_first_invoice_new_financial_year(self):
        # first invoice of a new FY: create_invoice with invoice_date=date(2027,4,1) gets number "INV/2027-28/0001"
        order = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 10)])
        approve_order(order, self.admin1)
        inv = create_invoice(order, self.admin1, invoice_date=date(2027, 4, 1))
        self.assertEqual(inv.number, "INV/2027-28/0001")


class CreditNoteAndAuditTests(TestCase):
    def setUp(self):
        call_command("seed_benchmark")
        self.supplier = Supplier.objects.create(name="Test Supplier", gstin="33AABBCCDDEE1Z5")
        self.flour = Item.objects.get(name="Flour")
        self.oil = Item.objects.get(name="Cooking oil")
        self.spice = Item.objects.get(name="Salt and spice mix")
        self.pouch = Item.objects.get(name="200 g pouch")
        self.carton = Item.objects.get(name="Carton")
        self.murukku = Item.objects.get(name="Murukku 200 g")
        self.bom = BOM.objects.get(product=self.murukku)

        # Buy 60 kg flour at Rs 45
        record_purchase(
            self.flour,
            "60",
            "kg",
            "45",
            self.supplier,
            date(2026, 11, 24),
            invoice_no="INV-SETUP",
        )

        # Run benchmark production run (actual_output 188)
        record_production_run(
            self.bom,
            188,
            {
                self.flour: {"qty": 40000},
                self.oil: {"qty": 10500},
                self.spice: {"qty": 1200},
                self.pouch: {"qty": 194, "wastage": 6},
                self.carton: {"qty": 9},
            },
        )
        self.assertEqual(get_balance(self.murukku), 238)

        User = get_user_model()
        self.salesman_a = User.objects.create_user(username="salesman_a", password="password")
        self.salesman_b = User.objects.create_user(username="salesman_b", password="password")
        self.admin1 = User.objects.create_user(username="admin1", password="password", is_staff=True)

        self.retailer_a = Customer.objects.get(name="Retailer A")
        self.distributor_b = Customer.objects.get(name="Distributor B")

    def test_cancel_invoice_after_dispatch(self):
        # Retailer A order 150 -> approve -> invoice (invoice_date date(2026,11,24)) -> dispatch (stock 88)
        order = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 150)])
        approve_order(order, self.admin1)
        invoice = create_invoice(order, self.admin1, invoice_date=date(2026, 11, 24))
        record_dispatch(invoice, self.admin1)
        self.assertEqual(get_balance(self.murukku), 88)

        # cancel_invoice(invoice, admin1, "Wrong customer", note_date=date(2026,11,24))
        cn = cancel_invoice(invoice, self.admin1, "Wrong customer", note_date=date(2026, 11, 24))

        # credit note number "CN/2026-27/0001"; amounts taxable 750000, cgst 18750, sgst 18750, igst 0, total 787500
        self.assertEqual(cn.number, "CN/2026-27/0001")
        self.assertEqual(cn.taxable_paise, 750000)
        self.assertEqual(cn.cgst_paise, 18750)
        self.assertEqual(cn.sgst_paise, 18750)
        self.assertEqual(cn.igst_paise, 0)
        self.assertEqual(cn.total_paise, 787500)

        # invoice.status CANCELLED; order.status CANCELLED; Murukku 200 g balance back to 238
        invoice.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.CANCELLED)
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertEqual(get_balance(self.murukku), 238)

        # exactly one SALE_RETURN ledger row with quantity +150 and value_paise +255567, source_type "CreditNote"
        sale_returns = StockLedger.objects.filter(movement_type=StockLedger.MovementType.SALE_RETURN)
        self.assertEqual(sale_returns.count(), 1)
        sr = sale_returns.first()
        self.assertEqual(sr.quantity, 150)
        self.assertEqual(sr.value_paise, 255567)
        self.assertEqual(sr.source_type, "CreditNote")
        self.assertEqual(sr.source_id, cn.id)

        # original DISPATCH row still exists unchanged
        dispatch_rows = StockLedger.objects.filter(movement_type=StockLedger.MovementType.DISPATCH, source_id=invoice.id)
        self.assertEqual(dispatch_rows.count(), 1)
        self.assertEqual(dispatch_rows.first().quantity, -150)

        # customer_outstanding(Retailer A) is 0
        self.assertEqual(customer_outstanding(self.retailer_a), 0)

    def test_cancel_invoice_before_dispatch(self):
        # Cancel before dispatch: no new ledger rows, stock stays 238, status CANCELLED
        order = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 100)])
        approve_order(order, self.admin1)
        invoice = create_invoice(order, self.admin1, invoice_date=date(2026, 11, 24))

        count_before = StockLedger.objects.count()
        cancel_invoice(invoice, self.admin1, "Order cancelled before dispatch")

        self.assertEqual(StockLedger.objects.count(), count_before)
        self.assertEqual(get_balance(self.murukku), 238)

        invoice.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.CANCELLED)
        self.assertEqual(order.status, Order.Status.CANCELLED)

    def test_cancel_invoice_validation_errors(self):
        # cancel_invoice raises ValueError when: the invoice has a payment; the invoice is already cancelled; the reason is empty
        order = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 50)])
        approve_order(order, self.admin1)
        invoice = create_invoice(order, self.admin1)

        # Record payment
        record_payment(invoice, 10000, date(2026, 11, 25), Payment.Mode.UPI, user=self.admin1)
        with self.assertRaises(ValueError):
            cancel_invoice(invoice, self.admin1, "Trying to cancel with payment")

        # Second invoice for already cancelled test
        order2 = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 20)])
        approve_order(order2, self.admin1)
        invoice2 = create_invoice(order2, self.admin1)

        # Empty reason
        with self.assertRaises(ValueError):
            cancel_invoice(invoice2, self.admin1, "")
        with self.assertRaises(ValueError):
            cancel_invoice(invoice2, self.admin1, "   ")

        # Valid cancellation
        cancel_invoice(invoice2, self.admin1, "First valid cancellation")

        # Already cancelled
        with self.assertRaises(ValueError):
            cancel_invoice(invoice2, self.admin1, "Already cancelled")

    def test_post_cancellation_actions_disallowed(self):
        # after cancelling, record_payment and record_dispatch on that invoice raise ValueError
        order = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 30)])
        approve_order(order, self.admin1)
        invoice = create_invoice(order, self.admin1)
        cancel_invoice(invoice, self.admin1, "Cancelled order")

        with self.assertRaises(ValueError):
            record_payment(invoice, 5000, date(2026, 11, 25), Payment.Mode.CASH, user=self.admin1)

        with self.assertRaises(ValueError):
            record_dispatch(invoice, self.admin1)

    def test_audit_logs(self):
        # Audit: after create_invoice there is an AuditLog row with action "INVOICE_CREATED", user admin1, model_name "Invoice", object_id = invoice id;
        # after cancel there is one with action "INVOICE_CANCELLED" and "Wrong customer" in details;
        # update_order_line changing price 5000 -> 4800 and quantity 150 -> 100 creates an "ORDER_LINE_EDITED" row whose details contain both "5000" and "4800";
        # record_production_run creates "PRODUCTION_RUN_CREATED"
        order = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 150)])

        update_order_line(order, self.murukku, 100, unit_price_paise=4800, user=self.admin1)
        edit_log = AuditLog.objects.filter(action="ORDER_LINE_EDITED").last()
        self.assertIsNotNone(edit_log)
        self.assertIn("5000", edit_log.details)
        self.assertIn("4800", edit_log.details)
        self.assertIn("150", edit_log.details)
        self.assertIn("100", edit_log.details)

        approve_order(order, self.admin1)
        invoice = create_invoice(order, self.admin1, invoice_date=date(2026, 11, 24))

        inv_log = AuditLog.objects.filter(action="INVOICE_CREATED", object_id=invoice.id).first()
        self.assertIsNotNone(inv_log)
        self.assertEqual(inv_log.user, self.admin1)
        self.assertEqual(inv_log.model_name, "Invoice")
        self.assertEqual(inv_log.details, invoice.number)

        cancel_invoice(invoice, self.admin1, "Wrong customer", note_date=date(2026, 11, 24))
        cancel_log = AuditLog.objects.filter(action="INVOICE_CANCELLED", object_id=invoice.id).first()
        self.assertIsNotNone(cancel_log)
        self.assertIn("Wrong customer", cancel_log.details)

        prod_log = AuditLog.objects.filter(action="PRODUCTION_RUN_CREATED").first()
        self.assertIsNotNone(prod_log)

    def test_failed_dispatch_leaves_no_audit_log(self):
        # a dispatch that fails for short stock (competing-orders case) leaves NO new AuditLog row for it
        order1 = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 150)])
        approve_order(order1, self.admin1)
        inv1 = create_invoice(order1, self.admin1, invoice_date=date(2026, 11, 24))

        order2 = place_order(self.distributor_b, self.salesman_b, [(self.murukku, 150)])
        approve_order(order2, self.admin1)
        inv2 = create_invoice(order2, self.admin1, invoice_date=date(2026, 11, 24))

        record_dispatch(inv1, self.admin1)
        audit_count_before = AuditLog.objects.count()

        with self.assertRaises(ValueError):
            record_dispatch(inv2, self.admin1)

        self.assertEqual(AuditLog.objects.count(), audit_count_before)

    def test_audit_log_append_only(self):
        # editing or deleting an AuditLog row raises an error
        log = AuditLog.objects.create(
            user=self.admin1,
            action="TEST_ACTION",
            model_name="TestModel",
            object_id=1,
            details="initial details",
        )

        log.details = "modified details"
        with self.assertRaises(RuntimeError):
            log.save()

        with self.assertRaises(RuntimeError):
            log.delete()

    def test_second_credit_note_new_financial_year(self):
        # second credit note of a new FY: an invoice created on date(2027,4,1) and cancelled with note_date=date(2027,4,2) gets "CN/2027-28/0001"
        order = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 20)])
        approve_order(order, self.admin1)
        invoice = create_invoice(order, self.admin1, invoice_date=date(2027, 4, 1))

        cn = cancel_invoice(invoice, self.admin1, "Return in new FY", note_date=date(2027, 4, 2))
        self.assertEqual(cn.number, "CN/2027-28/0001")


class PDFInvoiceTests(TestCase):
    def setUp(self):
        # Setup helper: seed benchmark, buy 60 kg flour at Rs 45, run benchmark production run
        call_command("seed_benchmark")
        self.supplier = Supplier.objects.create(name="Test Supplier", gstin="33AABBCCDDEE1Z5")
        self.flour = Item.objects.get(name="Flour")
        self.oil = Item.objects.get(name="Cooking oil")
        self.spice = Item.objects.get(name="Salt and spice mix")
        self.pouch = Item.objects.get(name="200 g pouch")
        self.carton = Item.objects.get(name="Carton")
        self.murukku = Item.objects.get(name="Murukku 200 g")
        self.bom = BOM.objects.get(product=self.murukku)

        # Buy 60 kg flour at Rs 45
        record_purchase(
            self.flour,
            "60",
            "kg",
            "45",
            self.supplier,
            date(2026, 11, 24),
            invoice_no="INV-SETUP",
        )

        # Run benchmark production run (actual_output 188)
        record_production_run(
            self.bom,
            188,
            {
                self.flour: {"qty": 40000},
                self.oil: {"qty": 10500},
                self.spice: {"qty": 1200},
                self.pouch: {"qty": 194, "wastage": 6},
                self.carton: {"qty": 9},
            },
        )
        self.assertEqual(get_balance(self.murukku), 238)

        User = get_user_model()
        self.salesman_a = User.objects.create_user(username="salesman_a", password="password")
        self.salesman_b = User.objects.create_user(username="salesman_b", password="password")
        self.admin1 = User.objects.create_user(username="admin1", password="password", is_staff=True)

        self.retailer_a = Customer.objects.get(name="Retailer A")
        self.distributor_b = Customer.objects.get(name="Distributor B")

    def test_format_rupees(self):
        # format_rupees(paise) -> str, integer arithmetic only, Indian digit grouping, always 2 decimals
        self.assertEqual(format_rupees(787500), "7,875.00")
        self.assertEqual(format_rupees(18750), "187.50")
        self.assertEqual(format_rupees(5), "0.05")
        self.assertEqual(format_rupees(0), "0.00")
        self.assertEqual(format_rupees(123456700), "12,34,567.00")

        # Negative numbers raise ValueError
        with self.assertRaises(ValueError):
            format_rupees(-1)
        with self.assertRaises(ValueError):
            format_rupees(-500)

    def test_format_rate(self):
        # format_rate(gst_rate_bp) -> str, integer arithmetic
        self.assertEqual(format_rate(500), "5%")
        self.assertEqual(format_rate(250), "2.5%")
        self.assertEqual(format_rate(1800), "18%")

        with self.assertRaises(ValueError):
            format_rate(-500)

    def test_retailer_a_pdf(self):
        # Uncompressed stream for raw byte inspection
        orig_compression = reportlab.rl_config.pageCompression
        reportlab.rl_config.pageCompression = 0
        self.addCleanup(setattr, reportlab.rl_config, "pageCompression", orig_compression)

        order = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 150)])
        approve_order(order, self.admin1)
        inv = create_invoice(order, self.admin1, invoice_date=date(2026, 11, 24))

        pdf_bytes = build_invoice_pdf(inv)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertIn(b"INV/2026-27/0001", pdf_bytes)
        self.assertIn(b"7,875.00", pdf_bytes)
        self.assertIn(b"187.50", pdf_bytes)
        self.assertIn(b"2106", pdf_bytes)
        self.assertIn(b"CGST", pdf_bytes)
        self.assertIn(b"SGST", pdf_bytes)
        self.assertNotIn(b"IGST", pdf_bytes)

    def test_distributor_b_pdf(self):
        # Uncompressed stream for raw byte inspection
        orig_compression = reportlab.rl_config.pageCompression
        reportlab.rl_config.pageCompression = 0
        self.addCleanup(setattr, reportlab.rl_config, "pageCompression", orig_compression)

        order = place_order(self.distributor_b, self.salesman_b, [(self.murukku, 88)])
        approve_order(order, self.admin1)
        inv = create_invoice(order, self.admin1, invoice_date=date(2026, 11, 24))

        pdf_bytes = build_invoice_pdf(inv)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertIn(b"IGST", pdf_bytes)
        self.assertIn(b"220.00", pdf_bytes)
        self.assertIn(b"4,620.00", pdf_bytes)
        self.assertNotIn(b"CGST", pdf_bytes)

    def test_cancelled_invoice_pdf(self):
        # Cancelled invoice PDF contains b"CANCELLED" and credit note number b"CN/2026-27/0001"
        orig_compression = reportlab.rl_config.pageCompression
        reportlab.rl_config.pageCompression = 0
        self.addCleanup(setattr, reportlab.rl_config, "pageCompression", orig_compression)

        order = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 150)])
        approve_order(order, self.admin1)
        inv = create_invoice(order, self.admin1, invoice_date=date(2026, 11, 24))
        cancel_invoice(inv, self.admin1, "Order cancelled by customer", note_date=date(2026, 11, 24))

        pdf_bytes = build_invoice_pdf(inv)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertIn(b"CANCELLED", pdf_bytes)
        self.assertIn(b"CN/2026-27/0001", pdf_bytes)

    def test_write_sample_pdf(self):
        # writes a sample for the repo docs
        order = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 150)])
        approve_order(order, self.admin1)
        inv = create_invoice(order, self.admin1, invoice_date=date(2026, 11, 24))

        pdf_bytes = build_invoice_pdf(inv)

        sample_path = os.path.normpath(os.path.join(settings.BASE_DIR, "..", "docs", "sample_invoice_retailer_a.pdf"))
        os.makedirs(os.path.dirname(sample_path), exist_ok=True)

        with open(sample_path, "wb") as f:
            f.write(pdf_bytes)

        self.assertTrue(os.path.exists(sample_path))
        self.assertGreater(os.path.getsize(sample_path), 0)


class ExpenseAndCostingTests(TestCase):
    def setUp(self):
        # Setup: seed benchmark, buy 60 kg flour at Rs 45, benchmark production run, user admin1
        call_command("seed_benchmark")
        self.supplier = Supplier.objects.create(name="Test Supplier", gstin="33AABBCCDDEE1Z5")
        self.flour = Item.objects.get(name="Flour")
        self.oil = Item.objects.get(name="Cooking oil")
        self.spice = Item.objects.get(name="Salt and spice mix")
        self.pouch = Item.objects.get(name="200 g pouch")
        self.carton = Item.objects.get(name="Carton")
        self.murukku = Item.objects.get(name="Murukku 200 g")
        self.bom = BOM.objects.get(product=self.murukku)

        # Buy 60 kg flour at Rs 45
        record_purchase(
            self.flour,
            "60",
            "kg",
            "45",
            self.supplier,
            date(2026, 11, 24),
            invoice_no="INV-SETUP",
        )

        User = get_user_model()
        self.admin1 = User.objects.create_user(username="admin1", password="password", is_staff=True)

        self.issued_lines = {
            self.flour: {"qty": 40000},
            self.oil: {"qty": 10500},
            self.spice: {"qty": 1200},
            self.pouch: {"qty": 194, "wastage": 6},
            self.carton: {"qty": 9},
        }

        # Benchmark production run (actual_output 188)
        self.run = record_production_run(
            self.bom,
            188,
            self.issued_lines,
            user=self.admin1,
        )

    def test_per_unit_paise(self):
        self.assertEqual(per_unit_paise(585500, 188), 3114)
        self.assertEqual(per_unit_paise(5, 2), 3)
        with self.assertRaises(ValueError):
            per_unit_paise(10, 0)
        with self.assertRaises(ValueError):
            per_unit_paise(10, -5)

    def test_run_costing_with_expenses(self):
        record_expense(self.run, "LABOUR", 120000, note="Shift labor", user=self.admin1)
        record_expense(self.run, "GAS_POWER", 60000, note="Gas consumption", user=self.admin1)

        costing = run_costing(self.run)
        self.assertEqual(costing["material_cost_paise"], 405500)
        self.assertEqual(costing["overhead_paise"], 180000)
        self.assertEqual(costing["total_cost_paise"], 585500)
        self.assertEqual(costing["good_packets"], 188)
        self.assertEqual(costing["cost_per_packet_paise"], 3114)
        self.assertEqual(costing["selling_price_paise"], 5000)
        self.assertEqual(costing["margin_per_packet_paise"], 1886)
        self.assertEqual(costing["margin_bp"], 3772)
        self.assertEqual(costing["overhead_by_kind"], {"LABOUR": 120000, "GAS_POWER": 60000})

    def test_run_costing_no_expenses(self):
        costing = run_costing(self.run)
        self.assertEqual(costing["overhead_paise"], 0)
        self.assertEqual(costing["total_cost_paise"], 405500)
        self.assertEqual(costing["cost_per_packet_paise"], 2157)

    def test_record_expense_validation_errors(self):
        expense_count_before = Expense.objects.count()
        audit_count_before = AuditLog.objects.count()

        with self.assertRaises(ValueError):
            record_expense(self.run, "LABOUR", 0, user=self.admin1)

        with self.assertRaises(ValueError):
            record_expense(self.run, "LABOUR", -100, user=self.admin1)

        with self.assertRaises(ValueError):
            record_expense(self.run, "UNKNOWN_KIND", 5000, user=self.admin1)

        self.assertEqual(Expense.objects.count(), expense_count_before)
        self.assertEqual(AuditLog.objects.count(), audit_count_before)

    def test_record_expense_creates_audit_log(self):
        exp = record_expense(self.run, "LABOUR", 120000, note="Factory shift", user=self.admin1)
        audit = AuditLog.objects.filter(action="EXPENSE_RECORDED", object_id=exp.id).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.user, self.admin1)
        self.assertEqual(audit.model_name, "Expense")
        self.assertIn("LABOUR", audit.details)
        self.assertIn("120000", audit.details)

    def test_run_costing_zero_actual_output(self):
        # A run with actual_output 0 (same issued quantities, actual_output 0)
        zero_run = record_production_run(
            self.bom,
            0,
            self.issued_lines,
            user=self.admin1,
        )
        costing = run_costing(zero_run)
        self.assertIsNone(costing["cost_per_packet_paise"])
        self.assertIsNone(costing["margin_per_packet_paise"])
        self.assertIsNone(costing["margin_bp"])


# ---------------------------------------------------------------------------
# Shared helper used by multiple Phase 5b test classes
# ---------------------------------------------------------------------------

def _setup_benchmark_full(test_case):
    """
    Seed benchmark, buy 60 kg flour at Rs 45, run production (actual 188),
    place + approve + create invoice for Retailer A (150 Murukku), dispatch,
    pay Rs 4000 on invoice, add LABOUR 120000 and GAS_POWER 60000 expenses.
    Returns (retailer_a, distributor_b, inv, run, admin1) on test_case.
    """
    tc = test_case
    call_command("seed_benchmark")
    tc.supplier = Supplier.objects.create(name="Test Supplier Phase5", gstin="33AACCBBDDEE1Z9")
    tc.flour = Item.objects.get(name="Flour")
    tc.oil = Item.objects.get(name="Cooking oil")
    tc.spice = Item.objects.get(name="Salt and spice mix")
    tc.pouch = Item.objects.get(name="200 g pouch")
    tc.carton = Item.objects.get(name="Carton")
    tc.murukku = Item.objects.get(name="Murukku 200 g")
    tc.bom = BOM.objects.get(product=tc.murukku)

    record_purchase(tc.flour, "60", "kg", "45", tc.supplier, date(2026, 11, 24), invoice_no="INV-P5")

    User = get_user_model()
    tc.admin1 = User.objects.create_user(username="admin1_p5", password="pw", is_staff=True)
    tc.salesman_a = User.objects.create_user(username="salesman_a_p5", password="pw")
    tc.salesman_b = User.objects.create_user(username="salesman_b_p5", password="pw")

    tc.retailer_a = Customer.objects.get(name="Retailer A")
    tc.distributor_b = Customer.objects.get(name="Distributor B")

    tc.run = record_production_run(
        tc.bom,
        188,
        {
            tc.flour: {"qty": 40000},
            tc.oil: {"qty": 10500},
            tc.spice: {"qty": 1200},
            tc.pouch: {"qty": 194, "wastage": 6},
            tc.carton: {"qty": 9},
        },
        user=tc.admin1,
    )

    order = place_order(tc.retailer_a, tc.salesman_a, [(tc.murukku, 150)])
    approve_order(order, tc.admin1)
    tc.inv_a = create_invoice(order, tc.admin1, invoice_date=date(2026, 11, 24))
    record_dispatch(tc.inv_a, tc.admin1)

    record_payment(tc.inv_a, 400000, date(2026, 11, 25), Payment.Mode.UPI, user=tc.admin1)

    record_expense(tc.run, "LABOUR", 120000, user=tc.admin1)
    record_expense(tc.run, "GAS_POWER", 60000, user=tc.admin1)


# ---------------------------------------------------------------------------
# Phase 5b - Report tests (single-invoice benchmark scenario)
# ---------------------------------------------------------------------------

class ReportsBaseTest(TestCase):
    """Tests for all reports using the standard benchmark state."""

    def setUp(self):
        _setup_benchmark_full(self)

    # --- stock_summary ---

    def test_stock_summary_values(self):
        report = stock_summary()
        self.assertEqual(report["title"], "Stock Summary")

        # Build lookup: item name -> row
        by_name = {r[0]: r for r in report["rows"]}

        # Columns: name, kind, unit, opening, receipts, issues, adjustments, closing
        # Flour: opening 100000, receipts 60000, issues 40000, closing 120000
        flour = by_name["Flour"]
        self.assertEqual(flour[3], 100000, "flour opening")
        self.assertEqual(flour[4], 60000, "flour receipts")
        self.assertEqual(flour[5], 40000, "flour issues")
        self.assertEqual(flour[6], 0, "flour adjustments")
        self.assertEqual(flour[7], 120000, "flour closing")

        # Oil: opening 30000, receipts 0, issues 10500
        oil = by_name["Cooking oil"]
        self.assertEqual(oil[3], 30000)
        self.assertEqual(oil[4], 0)
        self.assertEqual(oil[5], 10500)
        self.assertEqual(oil[7], 19500)

        # Spice: opening 5000, issues 1200
        spice = by_name["Salt and spice mix"]
        self.assertEqual(spice[3], 5000)
        self.assertEqual(spice[5], 1200)
        self.assertEqual(spice[7], 3800)

        # Pouches: opening 500, issues 194, closing 306
        pouch = by_name["200 g pouch"]
        self.assertEqual(pouch[3], 500)
        self.assertEqual(pouch[5], 194)
        self.assertEqual(pouch[7], 306)

        # Cartons: opening 30, issues 9, closing 21
        carton = by_name["Carton"]
        self.assertEqual(carton[3], 30)
        self.assertEqual(carton[5], 9)
        self.assertEqual(carton[7], 21)

        # Murukku: opening 50, receipts 188 (PROD_OUTPUT), issues 150 (DISPATCH)
        murukku = by_name["Murukku 200 g"]
        self.assertEqual(murukku[3], 50)
        self.assertEqual(murukku[4], 188)
        self.assertEqual(murukku[5], 150)
        self.assertEqual(murukku[7], 88)

    def test_stock_summary_identity(self):
        report = stock_summary()
        for row in report["rows"]:
            name, kind, unit, opening, receipts, issues, adj, closing = row
            self.assertEqual(
                opening + receipts - issues + adj, closing,
                f"Identity failed for {name}: {opening}+{receipts}-{issues}+{adj} != {closing}"
            )

    # --- stock_ledger ---

    def test_stock_ledger_flour(self):
        report = stock_ledger(self.flour)
        # 3 rows: OPENING 100000, PURCHASE +60000, PROD_ISSUE -40000
        self.assertEqual(len(report["rows"]), 3)
        final_balance = report["rows"][-1][6]  # running balance of last row
        self.assertEqual(final_balance, 120000)

    def test_stock_ledger_murukku(self):
        report = stock_ledger(self.murukku)
        # OPENING 50, PROD_OUTPUT +188, DISPATCH -150
        self.assertEqual(len(report["rows"]), 3)
        self.assertEqual(report["rows"][-1][6], 88)

    def test_stock_ledger_date_from_after_purchase(self):
        # StockLedger.created_at uses auto_now_add, so all movements in a test run share
        # today's date. We cannot separate events by day within one run.
        # Test: date_from = tomorrow -> 0 rows in period (all events are "before" and go to opening)
        from datetime import timedelta
        tomorrow = date.today() + timedelta(days=1)
        report = stock_ledger(self.flour, date_from=tomorrow)
        self.assertEqual(len(report["rows"]), 0, "No rows in period when date_from is tomorrow")

        # Test: date_from = today -> all 3 rows are in period (created_at date == today)
        today = date.today()
        report2 = stock_ledger(self.flour, date_from=today)
        self.assertEqual(len(report2["rows"]), 3, "All 3 rows present when date_from is today")
        self.assertEqual(report2["rows"][-1][6], 120000, "Final running balance")

    # --- sales_register ---

    def test_sales_register_one_invoice(self):
        report = sales_register()
        self.assertEqual(len(report["rows"]), 1)
        row = report["rows"][0]
        self.assertEqual(row[0], "INV/2026-27/0001")
        self.assertEqual(row[6], 750000)   # taxable
        self.assertEqual(row[7], 18750)    # cgst
        self.assertEqual(row[8], 18750)    # sgst
        self.assertEqual(row[9], 0)        # igst
        self.assertEqual(row[10], 787500)  # total

        # Totals row
        totals = report["totals"]
        self.assertEqual(totals[6], 750000)
        self.assertEqual(totals[10], 787500)

    def test_sales_register_date_filter_excludes(self):
        # Range that excludes 2026-11-24 → no rows, zero totals
        report = sales_register(date_from=date(2026, 11, 25), date_to=date(2026, 11, 30))
        self.assertEqual(len(report["rows"]), 0)
        self.assertEqual(report["totals"][6], 0)
        self.assertEqual(report["totals"][10], 0)

    # --- customer_sales ---

    def test_customer_sales(self):
        report = customer_sales()
        self.assertEqual(len(report["rows"]), 1)
        row = report["rows"][0]
        self.assertEqual(row[0], "Retailer A")
        self.assertEqual(row[2], 750000)   # taxable
        self.assertEqual(row[4], 787500)   # total

    # --- product_sales ---

    def test_product_sales(self):
        report = product_sales()
        self.assertEqual(len(report["rows"]), 1)
        row = report["rows"][0]
        self.assertEqual(row[1], 150)      # quantity
        self.assertEqual(row[2], 750000)   # taxable

    # --- gst_summary ---

    def test_gst_summary_intra(self):
        report = gst_summary()
        self.assertEqual(len(report["rows"]), 1)
        row = report["rows"][0]
        self.assertEqual(row[0], "Intra-state")
        self.assertEqual(row[1], 500)       # gst_rate_bp
        self.assertEqual(row[2], 750000)    # taxable
        self.assertEqual(row[3], 18750)     # cgst
        self.assertEqual(row[4], 18750)     # sgst
        self.assertEqual(row[5], 0)         # igst

        # GSTR-3B totals
        g3b = report["gstr3b"]  # [taxable, igst, cgst, sgst]
        self.assertEqual(g3b[0], 750000)
        self.assertEqual(g3b[1], 0)
        self.assertEqual(g3b[2], 18750)
        self.assertEqual(g3b[3], 18750)

    # --- purchase_register ---

    def test_purchase_register(self):
        report = purchase_register()
        # Only actual purchases (not opening stock)
        self.assertEqual(len(report["rows"]), 1)
        row = report["rows"][0]
        self.assertEqual(row[1], "Flour")     # item name
        self.assertEqual(row[2], 60000)       # quantity base (g)
        # Rate = 270000 paise total / 60 kg = 4500 paise/kg
        self.assertEqual(row[4], 4500)        # rate paise/kg
        self.assertEqual(row[5], 270000)      # total amount

    # --- production_report ---

    def test_production_report(self):
        report = production_report()
        # First row is the run summary row
        rows = report["rows"]
        run_row = rows[0]
        self.assertTrue(run_row[0].startswith("RUN#"))
        self.assertEqual(run_row[3], 200)   # expected_output
        self.assertEqual(run_row[4], 188)   # actual_output
        self.assertEqual(run_row[5], -600)  # yield_variance_bp
        self.assertEqual(run_row[6], "Yes") # yield_flagged
        self.assertEqual(run_row[8], 405500)  # material cost

        # Find material lines
        by_material = {r[2]: r for r in rows if r[2] != ""}
        flour = by_material["Flour"]
        self.assertEqual(flour[3], 40000)   # standard
        self.assertEqual(flour[4], 40000)   # actual
        self.assertEqual(flour[5], 0)       # variance_bp
        self.assertEqual(flour[6], "No")    # flagged

        oil = by_material["Cooking oil"]
        self.assertEqual(oil[3], 10000)
        self.assertEqual(oil[4], 10500)
        self.assertEqual(oil[5], 500)
        self.assertEqual(oil[6], "Yes")

        spice = by_material["Salt and spice mix"]
        self.assertEqual(spice[5], 0)
        self.assertEqual(spice[6], "No")

        pouch = by_material["200 g pouch"]
        self.assertEqual(pouch[3], 200)
        self.assertEqual(pouch[4], 194)
        self.assertEqual(pouch[5], -300)
        self.assertEqual(pouch[6], "No")  # -300 not flagged (not > 300)
        self.assertEqual(pouch[7], 6)    # wastage

        carton = by_material["Carton"]
        self.assertEqual(carton[3], 10)
        self.assertEqual(carton[4], 9)
        self.assertEqual(carton[5], -1000)
        self.assertEqual(carton[6], "Yes")

    # --- payments_report ---

    def test_payments_report(self):
        report = payments_report()
        self.assertEqual(len(report["rows"]), 1)
        row = report["rows"][0]
        self.assertEqual(row[2], "INV/2026-27/0001")
        self.assertEqual(row[3], 400000)
        self.assertEqual(report["totals"][3], 400000)

    # --- receivables_ageing ---

    def test_receivables_ageing_same_day(self):
        # as_of = invoice date: Retailer A outstanding 387500 in 0-30 bucket
        report = receivables_ageing(as_of=date(2026, 11, 24))
        self.assertEqual(len(report["rows"]), 1)
        row = report["rows"][0]
        self.assertEqual(row[0], "Retailer A")
        self.assertEqual(row[1], 387500)  # 0-30
        self.assertEqual(row[2], 0)       # 31-60
        self.assertEqual(row[3], 0)       # 61-90
        self.assertEqual(row[4], 0)       # 90+

    def test_receivables_ageing_45_days_later(self):
        # as_of = invoice_date + 45 → 31-60 bucket
        from datetime import timedelta
        report = receivables_ageing(as_of=date(2026, 11, 24) + __import__("datetime").timedelta(days=45))
        row = report["rows"][0]
        self.assertEqual(row[1], 0)       # 0-30 empty
        self.assertEqual(row[2], 387500)  # 31-60

    # --- no floats ---

    def test_no_floats_in_report_rows(self):
        reports = [
            sales_register(),
            stock_summary(),
            stock_ledger(self.flour),
            customer_sales(),
            product_sales(),
            gst_summary(),
            purchase_register(),
            production_report(),
            payments_report(),
            receivables_ageing(as_of=date(2026, 11, 24)),
        ]
        for report in reports:
            for row in report["rows"]:
                for val in row:
                    self.assertNotIsInstance(
                        val, float,
                        f"Float found in report '{report['title']}': {val}"
                    )

    # --- read-only: table counts unchanged ---

    def test_reports_are_read_only(self):
        from core.models import (
            StockLedger, Purchase, Invoice, Payment, AuditLog, Expense,
            ProductionRun, CreditNote,
        )
        counts_before = {
            "StockLedger": StockLedger.objects.count(),
            "Purchase": Purchase.objects.count(),
            "Invoice": Invoice.objects.count(),
            "Payment": Payment.objects.count(),
            "AuditLog": AuditLog.objects.count(),
            "Expense": Expense.objects.count(),
            "ProductionRun": ProductionRun.objects.count(),
            "CreditNote": CreditNote.objects.count(),
        }
        # Call all reports
        sales_register()
        stock_summary()
        stock_ledger(self.flour)
        customer_sales()
        product_sales()
        gst_summary()
        purchase_register()
        production_report()
        payments_report()
        receivables_ageing()

        for model_name, count in counts_before.items():
            current = eval(f"{model_name}.objects.count()")
            self.assertEqual(current, count, f"{model_name} count changed after reports!")

    # --- CSV tests ---

    def test_csv_sales_register_money_format(self):
        report = sales_register()
        csv_text = to_csv(report)
        # 787500 paise = 7875.00 rupees (no commas)
        self.assertIn("7875.00", csv_text)
        # 18750 paise = 187.50
        self.assertIn("187.50", csv_text)
        # 750000 paise = 7500.00
        self.assertIn("7500.00", csv_text)
        # Must NOT have commas inside money values
        import re
        for line in csv_text.splitlines()[1:]:  # skip header
            for field in line.split(","):
                # A money value shouldn't be like "7,875.00" – but CSV commas are field seps
                # We check no comma appears inside a numeric value that looks like money
                # By checking the field itself (after CSV split) has no comma:
                stripped = field.strip('"')
                if stripped and stripped[0].isdigit():
                    self.assertNotIn(",", stripped,
                                     f"Comma inside money value in CSV: {stripped!r}")

    def test_csv_sales_register_row_count(self):
        import csv as csv_mod
        report = sales_register()
        csv_text = to_csv(report)
        reader = list(csv_mod.reader(csv_text.splitlines()))
        # header + 1 data row + 1 totals row = 3
        self.assertEqual(len(reader), 3)

    def test_csv_stock_summary_display_units(self):
        import csv as csv_mod
        report = stock_summary()
        # Verify raw rows stay unchanged (integers in base units)
        by_name_raw = {r[0]: r for r in report["rows"]}
        self.assertEqual(by_name_raw["Flour"][2], "g")
        self.assertEqual(by_name_raw["Flour"][7], 120000)
        self.assertEqual(by_name_raw["Cooking oil"][2], "ml")
        self.assertEqual(by_name_raw["Cooking oil"][7], 19500)
        self.assertEqual(by_name_raw["Murukku 200 g"][2], "pcs")
        self.assertEqual(by_name_raw["Murukku 200 g"][7], 88)

        # Both to_csv and to_csv_stock_summary should display unit labels and converted numbers
        for csv_fn in (to_csv, to_csv_stock_summary):
            csv_text = csv_fn(report)
            reader = list(csv_mod.reader(csv_text.splitlines()))
            headers = reader[0]
            item_idx = headers.index("Item")
            unit_idx = headers.index("Unit")
            closing_idx = headers.index("Closing")

            by_name_csv = {row[item_idx]: row for row in reader[1:] if len(row) > max(unit_idx, closing_idx)}

            # Flour closing "120.000" with unit "kg"
            flour_row = by_name_csv["Flour"]
            self.assertEqual(flour_row[unit_idx], "kg")
            self.assertEqual(flour_row[closing_idx], "120.000")

            # Oil closing "19.500" with unit "L"
            oil_row = by_name_csv["Cooking oil"]
            self.assertEqual(oil_row[unit_idx], "L")
            self.assertEqual(oil_row[closing_idx], "19.500")

            # Murukku closing "88" with unit "pcs"
            murukku_row = by_name_csv["Murukku 200 g"]
            self.assertEqual(murukku_row[unit_idx], "pcs")
            self.assertEqual(murukku_row[closing_idx], "88")


# ---------------------------------------------------------------------------
# Phase 5b Scenario 2: Two invoices (Retailer A + Distributor B)
# ---------------------------------------------------------------------------

class ReportsTwoInvoicesTest(TestCase):
    """Scenario 2: add Distributor B invoice for 88 Murukku (inter-state)."""

    def setUp(self):
        _setup_benchmark_full(self)
        # Retailer A already invoiced in _setup_benchmark_full.
        # Now add Distributor B for 88 packets.
        order_b = place_order(self.distributor_b, self.salesman_b, [(self.murukku, 88)])
        approve_order(order_b, self.admin1)
        self.inv_b = create_invoice(order_b, self.admin1, invoice_date=date(2026, 11, 24))

    def test_sales_register_two_invoices(self):
        report = sales_register()
        self.assertEqual(len(report["rows"]), 2)

        inv_b_row = next(r for r in report["rows"] if r[0] == "INV/2026-27/0002")
        self.assertEqual(inv_b_row[6], 440000)   # taxable
        self.assertEqual(inv_b_row[7], 0)         # cgst
        self.assertEqual(inv_b_row[8], 0)         # sgst
        self.assertEqual(inv_b_row[9], 22000)     # igst
        self.assertEqual(inv_b_row[10], 462000)   # total

        # Totals: both ISSUED
        totals = report["totals"]
        self.assertEqual(totals[6], 1190000)   # 750000 + 440000
        self.assertEqual(totals[10], 1249500)  # 787500 + 462000

    def test_gst_summary_two_groups(self):
        report = gst_summary()
        self.assertEqual(len(report["rows"]), 2)

        by_type = {r[0]: r for r in report["rows"]}
        intra = by_type["Intra-state"]
        self.assertEqual(intra[2], 750000)
        self.assertEqual(intra[3], 18750)  # cgst
        self.assertEqual(intra[4], 18750)  # sgst

        inter = by_type["Inter-state"]
        self.assertEqual(inter[2], 440000)
        self.assertEqual(inter[5], 22000)  # igst


# ---------------------------------------------------------------------------
# Phase 5b Scenario 3: Cancelled invoice
# ---------------------------------------------------------------------------

class ReportsCancelledInvoiceTest(TestCase):
    """Scenario 3: cancel Retailer A invoice (fresh setup, no payment)."""

    def setUp(self):
        call_command("seed_benchmark")
        self.supplier = Supplier.objects.create(name="Supplier C3", gstin="33CCDDEE1234F1Z5")
        self.flour = Item.objects.get(name="Flour")
        self.oil = Item.objects.get(name="Cooking oil")
        self.spice = Item.objects.get(name="Salt and spice mix")
        self.pouch = Item.objects.get(name="200 g pouch")
        self.carton = Item.objects.get(name="Carton")
        self.murukku = Item.objects.get(name="Murukku 200 g")
        self.bom = BOM.objects.get(product=self.murukku)

        record_purchase(self.flour, "60", "kg", "45", self.supplier, date(2026, 11, 24))

        User = get_user_model()
        self.admin1 = User.objects.create_user(username="admin1_c3", password="pw", is_staff=True)
        self.salesman_a = User.objects.create_user(username="salesman_a_c3", password="pw")
        self.retailer_a = Customer.objects.get(name="Retailer A")

        record_production_run(
            self.bom,
            188,
            {
                self.flour: {"qty": 40000},
                self.oil: {"qty": 10500},
                self.spice: {"qty": 1200},
                self.pouch: {"qty": 194, "wastage": 6},
                self.carton: {"qty": 9},
            },
            user=self.admin1,
        )

        order = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 150)])
        approve_order(order, self.admin1)
        self.inv = create_invoice(order, self.admin1, invoice_date=date(2026, 11, 24))
        # No payment - so we can cancel
        self.cn = cancel_invoice(self.inv, self.admin1, "Test cancellation",
                                 note_date=date(2026, 11, 24))

    def test_sales_register_shows_cancelled(self):
        report = sales_register()
        self.assertEqual(len(report["rows"]), 1)
        row = report["rows"][0]
        self.assertEqual(row[5], "CANCELLED")  # status column

    def test_sales_register_totals_exclude_cancelled(self):
        report = sales_register()
        # Totals must be zero since the only invoice is cancelled
        self.assertEqual(report["totals"][6], 0)   # taxable
        self.assertEqual(report["totals"][10], 0)  # total

    def test_gst_summary_zero_for_cancelled(self):
        report = gst_summary()
        # No ISSUED invoices → no rows
        self.assertEqual(len(report["rows"]), 0)
        # Grand total zero
        g3b = report["gstr3b"]
        self.assertEqual(g3b[0], 0)
        self.assertEqual(g3b[1], 0)

    def test_gst_summary_credit_note_section(self):
        report = gst_summary()
        cn = report["credit_notes"]
        self.assertEqual(cn["count"], 1)
        self.assertEqual(cn["total_paise"], 787500)


# ---------------------------------------------------------------------------
# Phase 5b-fix: run_benchmark command test
# ---------------------------------------------------------------------------

class RunBenchmarkCommandTest(TestCase):
    def test_run_benchmark_command(self):
        import io
        # First seed initial benchmark data
        call_command("seed_benchmark")

        out = io.StringIO()
        call_command("run_benchmark", stdout=out)
        output = out.getvalue()

        # Assert output contains required step lines and balances
        self.assertIn("1. Purchase 60 kg flour at Rs 45/kg.", output)
        self.assertIn("Distributor B: shortage of 32 packets (88 available)", output)
        self.assertIn("flour 120000 g", output)
        self.assertIn("oil 19500 ml", output)
        self.assertIn("pouches 306", output)
        self.assertIn("cartons 21", output)
        self.assertIn("finished goods 88", output)
        self.assertIn("invoice total 787500", output)
        self.assertIn("outstanding 387500", output)
        self.assertIn("cost per packet 3114", output)
        self.assertIn("margin bp 3772", output)

        # Assert ledger balances directly
        flour = Item.objects.get(name="Flour")
        oil = Item.objects.get(name="Cooking oil")
        pouch = Item.objects.get(name="200 g pouch")
        carton = Item.objects.get(name="Carton")
        murukku = Item.objects.get(name="Murukku 200 g")

        self.assertEqual(get_balance(flour), 120000)
        self.assertEqual(get_balance(oil), 19500)
        self.assertEqual(get_balance(pouch), 306)
        self.assertEqual(get_balance(carton), 21)
        self.assertEqual(get_balance(murukku), 88)

        # Assert second call changes nothing and prints message
        out2 = io.StringIO()
        call_command("run_benchmark", stdout=out2)
        output2 = out2.getvalue()
        self.assertIn("Benchmark already run.", output2)

        # Re-verify ledger unchanged
        self.assertEqual(get_balance(flour), 120000)
        self.assertEqual(get_balance(oil), 19500)
        self.assertEqual(get_balance(pouch), 306)
        self.assertEqual(get_balance(carton), 21)
        self.assertEqual(get_balance(murukku), 88)
        self.assertEqual(Purchase.objects.count(), 1)
        self.assertEqual(ProductionRun.objects.count(), 1)
        self.assertEqual(Invoice.objects.count(), 1)


# ---------------------------------------------------------------------------
# Phase 6b-1: Admin screens tests
# ---------------------------------------------------------------------------

from django.test import Client
from django.urls import reverse

class AdminScreensTests(TestCase):
    def setUp(self):
        call_command("seed_benchmark")
        call_command("seed_users", verbosity=0)

        User = get_user_model()
        self.salesman_a = User.objects.get(username="salesman_a")
        self.admin1 = User.objects.get(username="admin1")

        self.supplier = Supplier.objects.create(name="Test Supplier", gstin="33AABBCCDDEE1Z5")
        self.flour = Item.objects.get(name="Flour")
        self.oil = Item.objects.get(name="Cooking oil")
        self.spice = Item.objects.get(name="Salt and spice mix")
        self.pouch = Item.objects.get(name="200 g pouch")
        self.carton = Item.objects.get(name="Carton")
        self.murukku = Item.objects.get(name="Murukku 200 g")
        self.bom = BOM.objects.get(product=self.murukku)

        # Buy 60 kg flour at Rs 45
        record_purchase(
            self.flour,
            "60",
            "kg",
            "45",
            self.supplier,
            date(2026, 11, 24),
            invoice_no="INV-SETUP",
            user=self.admin1,
        )

        # Benchmark production run (188 packets)
        record_production_run(
            self.bom,
            188,
            {
                self.flour: {"qty": 40000},
                self.oil: {"qty": 10500},
                self.spice: {"qty": 1200},
                self.pouch: {"qty": 194, "wastage": 6},
                self.carton: {"qty": 9},
            },
            user=self.admin1,
        )

        self.client = Client()

    def test_anonymous_and_role_access(self):
        # anonymous GET of the order list redirects to the login page
        resp = self.client.get(reverse("order_list"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("login"), resp.url)

        # a salesman user gets 403 on the order list
        self.client.force_login(self.salesman_a)
        resp_salesman = self.client.get(reverse("order_list"))
        self.assertEqual(resp_salesman.status_code, 403)

        # the admin user gets 200
        self.client.force_login(self.admin1)
        resp_admin = self.client.get(reverse("order_list"))
        self.assertEqual(resp_admin.status_code, 200)

        # the home page for the admin user contains the Orders link
        resp_home = self.client.get(reverse("home"))
        self.assertEqual(resp_home.status_code, 200)
        self.assertContains(resp_home, "Orders")
        self.assertContains(resp_home, reverse("order_list"))

    def test_retailer_a_flow(self):
        retailer_a = Customer.objects.get(name="Retailer A")
        # Retailer A flow through the Client: place the order with place_order (150 packets)
        order = place_order(retailer_a, self.salesman_a, [(self.murukku, 150)])

        self.client.force_login(self.admin1)

        # the admin POSTs approve
        resp_approve = self.client.post(reverse("order_detail", args=[order.id]), {"action": "approve"})
        self.assertEqual(resp_approve.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.APPROVED)

        # then create invoice
        resp_invoice = self.client.post(reverse("order_detail", args=[order.id]), {"action": "create_invoice"})
        self.assertEqual(resp_invoice.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.INVOICED)
        self.assertTrue(hasattr(order, "invoice"))
        inv = order.invoice
        # afterwards the invoice total is 787500
        self.assertEqual(inv.total_paise, 787500)

        # then dispatch
        resp_dispatch = self.client.post(reverse("order_detail", args=[order.id]), {"action": "record_dispatch"})
        self.assertEqual(resp_dispatch.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DISPATCHED)
        # the Murukku 200 g balance is 88
        self.assertEqual(get_balance(self.murukku), 88)

        # and the order detail page contains "7,875.00"
        resp_detail = self.client.get(reverse("order_detail", args=[order.id]))
        self.assertEqual(resp_detail.status_code, 200)
        self.assertContains(resp_detail, "7,875.00")

    def test_payment_recording_and_validation(self):
        retailer_a = Customer.objects.get(name="Retailer A")
        order = place_order(retailer_a, self.salesman_a, [(self.murukku, 150)])
        approve_order(order, user=self.admin1)
        inv = create_invoice(order, user=self.admin1)
        record_dispatch(inv, user=self.admin1)

        self.client.force_login(self.admin1)

        # posting "4000.555" or "abc" shows an error message and creates no Payment
        resp_err1 = self.client.post(
            reverse("invoice_detail", args=[inv.id]),
            {"action": "record_payment", "amount": "4000.555", "mode": "BANK", "date": "2026-11-26"},
        )
        self.assertEqual(resp_err1.status_code, 200)
        self.assertContains(resp_err1, "cannot have more than 2 decimal places")
        self.assertEqual(Payment.objects.filter(invoice=inv).count(), 0)

        resp_err2 = self.client.post(
            reverse("invoice_detail", args=[inv.id]),
            {"action": "record_payment", "amount": "abc", "mode": "BANK", "date": "2026-11-26"},
        )
        self.assertEqual(resp_err2.status_code, 200)
        self.assertContains(resp_err2, "Invalid amount format")
        self.assertEqual(Payment.objects.filter(invoice=inv).count(), 0)

        # posting a payment of "4000" rupees gives invoice_outstanding 387500
        resp_pay = self.client.post(
            reverse("invoice_detail", args=[inv.id]),
            {"action": "record_payment", "amount": "4000", "mode": "UPI", "date": "2026-11-26"},
        )
        self.assertEqual(resp_pay.status_code, 302)
        self.assertEqual(invoice_outstanding(inv), 387500)

    def test_distributor_b_shortage_and_line_edit(self):
        # Distributor B: place_order 120
        distributor_b = Customer.objects.get(name="Distributor B")

        # Murukku stock after setUp is 50 + 188 = 238
        # We need available stock to be 88 so ordering 120 creates shortage of 32
        # Let's place and dispatch an order of 150 first
        retailer_a = Customer.objects.get(name="Retailer A")
        order_a = place_order(retailer_a, self.salesman_a, [(self.murukku, 150)])
        approve_order(order_a, user=self.admin1)
        inv_a = create_invoice(order_a, user=self.admin1)
        record_dispatch(inv_a, user=self.admin1)
        self.assertEqual(get_balance(self.murukku), 88)

        # Place order for 120
        order_b = place_order(distributor_b, self.salesman_a, [(self.murukku, 120)])

        self.client.force_login(self.admin1)

        # the order detail page shows a shortage of 32
        resp_detail = self.client.get(reverse("order_detail", args=[order_b.id]))
        self.assertEqual(resp_detail.status_code, 200)
        self.assertContains(resp_detail, "32")

        # and the Approve POST does NOT approve it (status still SUBMITTED, the response page contains "32")
        resp_approve = self.client.post(reverse("order_detail", args=[order_b.id]), {"action": "approve"})
        self.assertEqual(resp_approve.status_code, 200)
        order_b.refresh_from_db()
        self.assertEqual(order_b.status, Order.Status.SUBMITTED)
        self.assertContains(resp_approve, "32")

        # after posting the line edit to quantity 88, approving works
        line = order_b.lines.get(product=self.murukku)
        resp_edit = self.client.post(
            reverse("order_detail", args=[order_b.id]),
            {"action": "update_lines", f"qty_{line.id}": "88", f"price_{line.id}": "50.00"},
        )
        self.assertEqual(resp_edit.status_code, 302)
        line.refresh_from_db()
        self.assertEqual(line.quantity, 88)

        resp_approve2 = self.client.post(reverse("order_detail", args=[order_b.id]), {"action": "approve"})
        self.assertEqual(resp_approve2.status_code, 302)
        order_b.refresh_from_db()
        self.assertEqual(order_b.status, Order.Status.APPROVED)

    def test_pdf_download(self):
        retailer_a = Customer.objects.get(name="Retailer A")
        order = place_order(retailer_a, self.salesman_a, [(self.murukku, 150)])
        approve_order(order, user=self.admin1)
        inv = create_invoice(order, user=self.admin1)

        self.client.force_login(self.admin1)

        # PDF download: GET returns status 200, content type application/pdf,
        # the body starts with b"%PDF", and the Content-Disposition header contains "attachment"
        resp = self.client.get(reverse("download_invoice_pdf", args=[inv.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertTrue(resp.content.startswith(b"%PDF"))
        self.assertIn("attachment", resp["Content-Disposition"])

    def test_invoice_cancellation(self):
        retailer_a = Customer.objects.get(name="Retailer A")
        order = place_order(retailer_a, self.salesman_a, [(self.murukku, 150)])
        approve_order(order, user=self.admin1)
        inv = create_invoice(order, user=self.admin1)
        record_dispatch(inv, user=self.admin1)
        self.assertEqual(get_balance(self.murukku), 88)

        self.client.force_login(self.admin1)

        # cancel with an empty reason shows an error and does not cancel
        resp_empty = self.client.post(
            reverse("invoice_detail", args=[inv.id]),
            {"action": "cancel_invoice", "reason": "   "},
        )
        self.assertEqual(resp_empty.status_code, 200)
        self.assertContains(resp_empty, "Cancellation reason must not be empty")
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.Status.ISSUED)
        self.assertEqual(get_balance(self.murukku), 88)

        # with a reason it cancels and the stock returns
        resp_cancel = self.client.post(
            reverse("invoice_detail", args=[inv.id]),
            {"action": "cancel_invoice", "reason": "Wrong billing address"},
        )
        self.assertEqual(resp_cancel.status_code, 302)
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.Status.CANCELLED)
        self.assertEqual(get_balance(self.murukku), 238)




