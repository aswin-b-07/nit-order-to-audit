from datetime import date
from decimal import Decimal
from django.test import TestCase, Client
from django.core.management import call_command
from django.contrib.auth import get_user_model

from core.models import Item, Supplier, BOM, ProductionRun, Purchase, StockLedger
from core.services import get_balance

User = get_user_model()


class ProductionWebTestCase(TestCase):
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

        self.client = Client()

    def test_anonymous_redirect(self):
        # Anonymous users go to /login/
        for url in ["/purchase/new/", "/purchases/", "/production/new/", "/production/"]:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302)
            self.assertTrue(resp["Location"].startswith("/login/"))

    def test_role_access_forbidden(self):
        # salesman_a, admin1, accountant1 and owner1 get 403 on /purchase/new/ and /production/new/
        non_prod_users = [self.salesman_a, self.admin1, self.accountant1, self.owner1]
        for user in non_prod_users:
            self.client.force_login(user)
            resp_p = self.client.get("/purchase/new/")
            self.assertEqual(resp_p.status_code, 403, f"Expected 403 for {user.username} on /purchase/new/")
            resp_pr = self.client.get("/production/new/")
            self.assertEqual(resp_pr.status_code, 403, f"Expected 403 for {user.username} on /production/new/")

    def test_nav_links_visibility(self):
        # Production links in nav only for production role and superuser
        self.client.force_login(self.production1)
        resp = self.client.get("/")
        self.assertContains(resp, "Purchase")
        self.assertContains(resp, "Production")

        superuser = User.objects.create_superuser("super", "super@example.com", "pass")
        self.client.force_login(superuser)
        resp = self.client.get("/")
        self.assertContains(resp, "Purchase")
        self.assertContains(resp, "Production")

        for user in [self.salesman_a, self.admin1, self.accountant1, self.owner1]:
            self.client.force_login(user)
            resp = self.client.get("/")
            # Check navbar links
            content = resp.content.decode("utf-8")
            self.assertNotIn('href="/purchases/"', content)
            self.assertNotIn('href="/production/"', content)

    def test_invalid_purchase_input(self):
        # invalid purchase input (0, negative, "abc") creates no row and no ledger entry
        self.client.force_login(self.production1)
        initial_purchases_count = Purchase.objects.count()
        initial_ledger_count = StockLedger.objects.count()

        invalid_inputs = [
            {"quantity": "0", "rate": "45"},
            {"quantity": "-10", "rate": "45"},
            {"quantity": "abc", "rate": "45"},
            {"quantity": "10", "rate": "0"},
            {"quantity": "10", "rate": "-45"},
            {"quantity": "10", "rate": "xyz"},
        ]

        for data in invalid_inputs:
            post_data = {
                "supplier": str(self.supplier.id),
                "item": str(self.flour.id),
                "quantity": data["quantity"],
                "rate": data["rate"],
            }
            resp = self.client.post("/purchase/new/", post_data)
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "Record Purchase")
            self.assertEqual(Purchase.objects.count(), initial_purchases_count)
            self.assertEqual(StockLedger.objects.count(), initial_ledger_count)

    def test_benchmark_purchase_and_production_flow(self):
        self.client.force_login(self.production1)

        # 1. production1 posts a purchase of 60 kg flour at Rs 45/kg -> flour stock 160000 g.
        self.assertEqual(get_balance(self.flour), 100000)
        purchase_data = {
            "supplier": str(self.supplier.id),
            "item": str(self.flour.id),
            "quantity": "60",
            "rate": "45",
            "invoice_no": "INV-P1",
        }
        resp_p = self.client.post("/purchase/new/", purchase_data, follow=True)
        self.assertEqual(resp_p.status_code, 200)
        self.assertRedirects(resp_p, "/purchases/")
        self.assertEqual(get_balance(self.flour), 160000)

        # 2. production1 posts the run: flour 40 kg, oil 10.5 L, spice 1.2 kg, good 188, damaged 6, cartons 9
        # -> flour 120000 g, oil 19500 ml, pouches 306, cartons 21, finished goods 238.
        run_data = {
            "product": str(self.murukku.id),
            f"item_{self.flour.id}": "40",
            f"item_{self.oil.id}": "10.5",
            f"item_{self.spice.id}": "1.2",
            "actual_good_packets": "188",
            "damaged_pouches": "6",
            "cartons_used": "9",
        }
        resp_r = self.client.post("/production/new/", run_data, follow=True)
        self.assertEqual(resp_r.status_code, 200)

        run = ProductionRun.objects.order_by("-id").first()
        self.assertIsNotNone(run)
        self.assertRedirects(resp_r, f"/production/{run.id}/")

        # Verify stock balances:
        # flour: 160000 - 40000 = 120000 g
        self.assertEqual(get_balance(self.flour), 120000)
        # oil: 30000 - 10500 = 19500 ml
        self.assertEqual(get_balance(self.oil), 19500)
        # pouches: 500 - (188 + 6) = 306 pcs
        self.assertEqual(get_balance(self.pouch), 306)
        # cartons: 30 - 9 = 21 pcs
        self.assertEqual(get_balance(self.carton), 21)
        # finished goods (Murukku): 50 + 188 = 238 pcs
        self.assertEqual(get_balance(self.murukku), 238)

        # 3. The detail page contains the oil +5.00% and the yield -6.00% FLAGGED markers;
        # pouches -3.00% is NOT flagged; cartons -10.00% is flagged.
        detail_html = resp_r.content.decode("utf-8")
        self.assertIn("+5.00%", detail_html)
        self.assertIn("-6.00%", detail_html)
        self.assertIn("-3.00%", detail_html)
        self.assertIn("-10.00%", detail_html)
        self.assertIn("FLAGGED", detail_html)

        # Verify specific line flags
        oil_line = run.lines.get(item=self.oil)
        self.assertEqual(oil_line.variance_bp, 500)
        self.assertTrue(oil_line.flagged)

        pouch_line = run.lines.get(item=self.pouch)
        self.assertEqual(pouch_line.variance_bp, -300)
        self.assertFalse(pouch_line.flagged)

        carton_line = run.lines.get(item=self.carton)
        self.assertEqual(carton_line.variance_bp, -1000)
        self.assertTrue(carton_line.flagged)

        self.assertEqual(run.yield_variance_bp, -600)
        self.assertTrue(run.yield_flagged)

        # 4. /production/ lists recent runs with links.
        resp_list = self.client.get("/production/")
        self.assertEqual(resp_list.status_code, 200)
        self.assertContains(resp_list, f"/production/{run.id}/")
        self.assertContains(resp_list, "Murukku 200 g")
