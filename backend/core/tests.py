from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

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
        update_order_line(order2, self.murukku, 88)
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
            update_order_line(order1, self.murukku, 100)

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
        pay = record_payment(inv, 400000, date(2026, 11, 25), Payment.Mode.UPI)
        self.assertIsNotNone(pay.id)
        self.assertEqual(invoice_outstanding(inv), 387500)
        self.assertEqual(customer_outstanding(self.retailer_a), 387500)

        with self.assertRaises(ValueError):
            record_payment(inv, 387501, date(2026, 11, 26), Payment.Mode.BANK)

        with self.assertRaises(ValueError):
            record_payment(inv, 0, date(2026, 11, 26), Payment.Mode.CASH)

    def test_distributor_b_interstate_invoice(self):
        # Distributor B: place_order 120, approve_order raises ValueError containing "32";
        # update_order_line to 88; approve_order;
        # create_invoice(..., invoice_date=date(2026,11,24)):
        # number "INV/2026-27/0002", supply_type INTER, taxable_paise 440000,
        # igst_paise 22000, cgst_paise 0, sgst_paise 0, total_paise 462000
        # (Note: we first invoice order1 for Retailer A to get seq 1)
        order_a = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 150)])
        approve_order(order_a, self.admin1)
        create_invoice(order_a, self.admin1, invoice_date=date(2026, 11, 24))
        record_dispatch(order_a.invoice, self.admin1)  # Murukku stock is now 88

        order_b = place_order(self.distributor_b, self.salesman_b, [(self.murukku, 120)])
        with self.assertRaises(ValueError) as ctx:
            approve_order(order_b, self.admin1)
        self.assertIn("32", str(ctx.exception))

        update_order_line(order_b, self.murukku, 88)
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
