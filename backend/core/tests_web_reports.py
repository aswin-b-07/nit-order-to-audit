from datetime import date
from django.test import TestCase, Client
from django.core.management import call_command
from django.contrib.auth import get_user_model

from core.models import (
    Customer,
    Item,
    Supplier,
    BOM,
    Payment,
)
from core.services import (
    record_purchase,
    record_production_run,
    place_order,
    approve_order,
    create_invoice,
    record_dispatch,
    record_payment,
)

User = get_user_model()


class WebReportsTestCase(TestCase):
    def setUp(self):
        call_command("seed_users", verbosity=0)
        call_command("seed_benchmark", verbosity=0)

        self.accountant1 = User.objects.get(username="accountant1")
        self.owner1 = User.objects.get(username="owner1")
        self.admin1 = User.objects.get(username="admin1")
        self.salesman_a = User.objects.get(username="salesman_a")
        self.production1 = User.objects.get(username="production1")

        self.superuser = User.objects.create_superuser(
            username="superadmin", email="super@example.com", password="password"
        )

        supplier = Supplier.objects.create(
            name="Benchmark Supplier", gstin="33AABBCCDDEE1Z5"
        )
        flour = Item.objects.get(name="Flour")
        oil = Item.objects.get(name="Cooking oil")
        spice = Item.objects.get(name="Salt and spice mix")
        pouch = Item.objects.get(name="200 g pouch")
        carton = Item.objects.get(name="Carton")
        murukku = Item.objects.get(name="Murukku 200 g")
        bom = BOM.objects.get(product=murukku)
        retailer_a = Customer.objects.get(name="Retailer A")

        # 1. Purchase: 60 kg flour at Rs 45/kg
        record_purchase(
            flour,
            "60",
            "kg",
            "45",
            supplier,
            purchase_date=date(2026, 11, 24),
            invoice_no="INV-BM-01",
            user=self.admin1,
        )

        # 2. Production run: Murukku 200 g
        run = record_production_run(
            bom,
            188,
            {
                flour: {"qty": 40000},
                oil: {"qty": 10500},
                spice: {"qty": 1200},
                pouch: {"qty": 194, "wastage": 6},
                carton: {"qty": 9},
            },
            user=self.admin1,
        )

        # 3. Order: Retailer A 150 packets
        order = place_order(retailer_a, self.salesman_a, [(murukku, 150)])

        # 4. Approve, Invoice, Dispatch
        approve_order(order, user=self.admin1)
        inv = create_invoice(order, user=self.admin1, invoice_date=date(2026, 11, 25))
        record_dispatch(inv, user=self.admin1)
        self.invoice = inv

        # 5. Payment: Rs 4,000 from Retailer A
        record_payment(inv, 400000, date(2026, 11, 26), Payment.Mode.UPI, user=self.admin1)

        self.client = Client()

    def test_anonymous_redirect(self):
        # Anonymous users go to /login/
        resp = self.client.get("/reports/")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].startswith("/login/"))

        resp_csv = self.client.get("/reports/sales-register/csv/")
        self.assertEqual(resp_csv.status_code, 302)
        self.assertTrue(resp_csv["Location"].startswith("/login/"))

    def test_salesman_and_production_forbidden(self):
        # Salesman gets 403
        self.client.force_login(self.salesman_a)
        resp = self.client.get("/reports/")
        self.assertEqual(resp.status_code, 403)

        resp_csv = self.client.get("/reports/sales-register/csv/")
        self.assertEqual(resp_csv.status_code, 403)

        # Production gets 403
        self.client.force_login(self.production1)
        resp = self.client.get("/reports/")
        self.assertEqual(resp.status_code, 403)

        resp_csv = self.client.get("/reports/sales-register/csv/")
        self.assertEqual(resp_csv.status_code, 403)

    def test_authorized_roles_can_view_reports_page(self):
        for user in [self.accountant1, self.owner1, self.superuser]:
            self.client.force_login(user)
            resp = self.client.get("/reports/")
            self.assertEqual(resp.status_code, 200, f"Failed for {user.username}")
            self.assertContains(resp, "Sales Register")
            self.assertContains(resp, "Stock Summary")
            self.assertContains(resp, "Stock Ledger")
            self.assertContains(resp, "Customer Sales")
            self.assertContains(resp, "Product Sales")
            self.assertContains(resp, "GST Summary")
            self.assertContains(resp, "Purchase Register")
            self.assertContains(resp, "Production Report")
            self.assertContains(resp, "Payments Report")
            self.assertContains(resp, "Receivables Ageing")

        # Admin gets 403 because 'view_reports' is missing from ROLE_ADMIN in permissions.py
        self.client.force_login(self.admin1)
        resp_admin = self.client.get("/reports/")
        self.assertEqual(resp_admin.status_code, 403)

    def test_accountant_read_only_no_post(self):
        # The accountant has read-only access: no POST anywhere
        self.client.force_login(self.accountant1)
        resp_post = self.client.post("/reports/", {})
        self.assertEqual(resp_post.status_code, 405)

        resp_post_csv = self.client.post("/reports/sales-register/csv/", {})
        self.assertEqual(resp_post_csv.status_code, 405)

    def test_sales_register_csv_contains_invoice_total(self):
        self.client.force_login(self.accountant1)
        resp = self.client.get("/reports/sales-register/csv/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment;", resp["Content-Disposition"])
        self.assertIn("sales_register.csv", resp["Content-Disposition"])

        content = resp.content.decode("utf-8")
        # Check: sales register CSV contains invoice total 7875.00 or 787500
        self.assertTrue("7875.00" in content or "787500" in content, f"Invoice total not found in: {content}")

    def test_all_reports_csv_download(self):
        self.client.force_login(self.accountant1)
        reports = [
            "sales-register",
            "stock-summary",
            "stock-ledger",
            "customer-sales",
            "product-sales",
            "gst-summary",
            "purchase-register",
            "production-report",
            "payments",
            "receivables-ageing",
        ]
        for name in reports:
            resp = self.client.get(f"/reports/{name}/csv/")
            self.assertEqual(resp.status_code, 200, f"CSV download failed for {name}")
            self.assertEqual(resp["Content-Type"], "text/csv; charset=utf-8")
            self.assertIn("attachment;", resp["Content-Disposition"])

    def test_invalid_date_handled_not_500(self):
        self.client.force_login(self.accountant1)
        # Invalid date on /reports/ returns error message or is ignored, not 500
        resp = self.client.get("/reports/?from=invalid-date&to=2026-12-31")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Invalid")

        # Invalid date on /reports/<name>/csv/ returns 200 (ignored/handled gracefully), not 500
        resp_csv = self.client.get("/reports/sales-register/csv/?from=bad-date")
        self.assertEqual(resp_csv.status_code, 200)

    def test_date_filter_applied_to_reports(self):
        self.client.force_login(self.accountant1)
        # Date filter outside the invoice date
        resp_csv = self.client.get("/reports/sales-register/csv/?from=2027-01-01&to=2027-01-31")
        self.assertEqual(resp_csv.status_code, 200)
        content = resp_csv.content.decode("utf-8")
        self.assertNotIn("7875.00", content)

    def test_nav_reports_link(self):
        # Accountant, owner, superuser have view_reports in permissions.py
        for user in [self.accountant1, self.owner1, self.superuser]:
            self.client.force_login(user)
            resp = self.client.get("/")
            self.assertContains(resp, 'href="/reports/"')

        # Admin, salesman and production do not have view_reports in permissions.py
        for user in [self.admin1, self.salesman_a, self.production1]:
            self.client.force_login(user)
            resp = self.client.get("/")
            self.assertNotContains(resp, 'href="/reports/"')
