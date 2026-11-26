from datetime import date
from django.test import TestCase, Client
from django.core.management import call_command
from django.contrib.auth import get_user_model

from core.models import Item, Supplier, BOM, ProductionRun, Expense
from core.services import record_purchase, record_production_run

User = get_user_model()


class ProductionCostingWebTestCase(TestCase):
    def setUp(self):
        call_command("seed_users", verbosity=0)
        call_command("seed_benchmark", verbosity=0)

        self.salesman_a = User.objects.get(username="salesman_a")
        self.salesman_b = User.objects.get(username="salesman_b")
        self.admin1 = User.objects.get(username="admin1")
        self.production1 = User.objects.get(username="production1")
        self.accountant1 = User.objects.get(username="accountant1")
        self.owner1 = User.objects.get(username="owner1")

        self.supplier = Supplier.objects.create(name="Benchmark Supplier", gstin="33AABBCCDDEE1Z5")
        self.flour = Item.objects.get(name="Flour")
        self.oil = Item.objects.get(name="Cooking oil")
        self.spice = Item.objects.get(name="Salt and spice mix")
        self.pouch = Item.objects.get(name="200 g pouch")
        self.carton = Item.objects.get(name="Carton")
        self.murukku = Item.objects.get(name="Murukku 200 g")
        self.bom = BOM.objects.get(product=self.murukku)

        # Benchmark setup:
        # purchase 60 kg flour at Rs 45/kg
        record_purchase(
            self.flour,
            "60",
            "kg",
            "45",
            self.supplier,
            date(2026, 11, 24),
            invoice_no="INV-SETUP",
        )
        # production run: flour 40 kg, oil 10.5 L, spice 1.2 kg, good 188, damaged 6, cartons 9
        self.run = record_production_run(
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

        self.client = Client()

    def test_anonymous_redirect(self):
        # Anonymous users go to /login/
        resp_get = self.client.get(f"/production/{self.run.id}/")
        self.assertEqual(resp_get.status_code, 302)
        self.assertTrue(resp_get["Location"].startswith("/login/"))

        resp_post = self.client.post(
            f"/production/{self.run.id}/",
            {"kind": "LABOUR", "amount": "1200"},
        )
        self.assertEqual(resp_post.status_code, 302)
        self.assertTrue(resp_post["Location"].startswith("/login/"))

    def test_other_roles_forbidden(self):
        # salesman_a and admin1 (as well as accountant1 and owner1) get 403 when viewing or posting an expense
        for user in [self.salesman_a, self.admin1, self.accountant1, self.owner1]:
            self.client.force_login(user)
            resp_get = self.client.get(f"/production/{self.run.id}/")
            self.assertEqual(resp_get.status_code, 403, f"Expected 403 for GET by {user.username}")

            resp_post = self.client.post(
                f"/production/{self.run.id}/",
                {"kind": "LABOUR", "amount": "1200"},
            )
            self.assertEqual(resp_post.status_code, 403, f"Expected 403 for POST by {user.username}")

    def test_invalid_expense_amounts(self):
        # invalid amounts (0, -5, "abc", "10.555") create no expense row
        self.client.force_login(self.production1)
        initial_expense_count = Expense.objects.filter(run=self.run).count()

        invalid_amounts = ["0", "-5", "abc", "10.555", ""]
        for amt in invalid_amounts:
            resp = self.client.post(
                f"/production/{self.run.id}/",
                {"kind": "LABOUR", "amount": amt, "note": "test"},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(
                Expense.objects.filter(run=self.run).count(),
                initial_expense_count,
                f"Expense created unexpectedly for amount '{amt}'",
            )
            self.assertContains(resp, "alert-error")

    def test_benchmark_expenses_and_costing(self):
        # production1 posts labour Rs 1200 and gas/power Rs 600 -> the detail page shows cost per packet 31.14 (3114 paise) and margin 37.72%.
        self.client.force_login(self.production1)

        # 1. Post labour Rs 1200
        resp_labour = self.client.post(
            f"/production/{self.run.id}/",
            {"kind": "LABOUR", "amount": "1200", "note": "Labour shift 1"},
            follow=True,
        )
        self.assertEqual(resp_labour.status_code, 200)
        self.assertRedirects(resp_labour, f"/production/{self.run.id}/")
        self.assertTrue(Expense.objects.filter(run=self.run, kind="LABOUR", amount_paise=120000).exists())

        # 2. Post gas/power Rs 600
        resp_gas = self.client.post(
            f"/production/{self.run.id}/",
            {"kind": "GAS_POWER", "amount": "600", "note": "Gas cylinders"},
            follow=True,
        )
        self.assertEqual(resp_gas.status_code, 200)
        self.assertRedirects(resp_gas, f"/production/{self.run.id}/")
        self.assertTrue(Expense.objects.filter(run=self.run, kind="GAS_POWER", amount_paise=60000).exists())

        # 3. Check detail page contents
        detail_html = resp_gas.content.decode("utf-8")

        # Valuation note: "Weighted-average costing; overheads divided by good packets."
        self.assertIn("Weighted-average costing; overheads divided by good packets.", detail_html)

        # Cost per packet 31.14 (or 31.14 / 3114 paise)
        self.assertIn("31.14", detail_html)

        # Margin 37.72%
        self.assertIn("37.72%", detail_html)

        # Raw material cost, packaging cost, labour, gas/power, total cost, good packets, selling price
        # Material: 405500 paise -> Rs 4,055.00 (raw 343500: 3435.00, packaging 62000: 620.00)
        self.assertIn("3,435.00", detail_html)
        self.assertIn("620.00", detail_html)
        self.assertIn("1,200.00", detail_html)
        self.assertIn("600.00", detail_html)
        self.assertIn("5,855.00", detail_html)
        self.assertIn("188", detail_html)
        self.assertIn("50.00", detail_html)

        # Expenses list displayed
        self.assertIn("Labour shift 1", detail_html)
        self.assertIn("Gas cylinders", detail_html)
