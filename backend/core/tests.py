from decimal import Decimal
from django.core.management import call_command
from django.test import TestCase

from core.models import Item, StockLedger
from core.services import get_balance, to_base


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
