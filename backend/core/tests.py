from datetime import date
from decimal import Decimal
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
