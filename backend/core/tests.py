from datetime import date
from decimal import Decimal
from django.core.management import call_command
from django.test import TestCase

from core.models import Item, StockLedger, Supplier, Purchase
from core.services import (
    get_balance,
    get_value,
    to_base,
    value_for_issue,
    record_purchase,
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
