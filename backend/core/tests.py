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


