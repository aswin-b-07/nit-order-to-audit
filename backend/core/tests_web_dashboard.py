from datetime import date
from django.test import TestCase, Client
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from core.models import Item, Supplier, BOM, Customer, Payment, Expense
from core.services import (
    record_purchase,
    record_production_run,
    record_expense,
    place_order,
    approve_order,
    create_invoice,
    record_dispatch,
    record_payment,
)
from core.views import _format_base_quantity

User = get_user_model()


class DashboardWebTestCase(TestCase):
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
        self.retailer_a = Customer.objects.get(name="Retailer A")
        self.distributor_b = Customer.objects.get(name="Distributor B")

        # Purchase 60 kg flour at Rs 45
        record_purchase(
            self.flour,
            "60",
            "kg",
            "45",
            self.supplier,
            timezone.localdate(),
            invoice_no="INV-SETUP",
            user=self.admin1,
        )

        # Benchmark production run (188 packets)
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
            user=self.admin1,
        )
        record_expense(self.run, Expense.Kind.LABOUR, 120000, note="Labour", user=self.admin1)
        record_expense(self.run, Expense.Kind.GAS_POWER, 60000, note="Gas/Power", user=self.admin1)

        # Retailer A order 150 approved, invoiced, dispatched, payment Rs 4000
        order_a = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 150)])
        approve_order(order_a, user=self.admin1)
        inv_a = create_invoice(order_a, user=self.admin1)
        record_dispatch(inv_a, user=self.admin1)
        record_payment(inv_a, 400000, timezone.localdate(), Payment.Mode.BANK, "REF1", user=self.admin1)

        # Distributor B order of 120 placed (not approved)
        order_b = place_order(self.distributor_b, self.salesman_a, [(self.murukku, 120)])

        self.client = Client()

    def test_anonymous_and_role_access(self):
        # Anonymous redirects to login
        resp_anon = self.client.get(reverse("dashboard"))
        self.assertEqual(resp_anon.status_code, 302)
        self.assertIn(reverse("login"), resp_anon.url)

        # Owner gets 200
        self.client.force_login(self.owner1)
        resp_owner = self.client.get(reverse("dashboard"))
        self.assertEqual(resp_owner.status_code, 200)

        # Every other seeded role that lacks view_dashboard gets 403
        for user in [self.salesman_a, self.salesman_b, self.admin1, self.production1, self.accountant1]:
            self.client.force_login(user)
            resp = self.client.get(reverse("dashboard"))
            self.assertEqual(resp.status_code, 403, f"Expected 403 for {user.username}")

    def test_dashboard_content(self):
        self.client.force_login(self.owner1)
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)

        # 2 orders today with total "13,500.00"
        self.assertContains(resp, "13,500.00")
        self.assertContains(resp, "2")

        # outstanding total "3,875.00"
        self.assertContains(resp, "3,875.00")

        # cost per packet "31.14"
        self.assertContains(resp, "31.14")

        # flagged run with "-6.00%"
        self.assertContains(resp, "-6.00%")

        # Murukku 200 g stock as 88 pieces (read the display helper to assert its real format)
        expected_stock_str = _format_base_quantity(88, self.murukku.base_unit)
        self.assertContains(resp, expected_stock_str)


class DashboardEmptyStateTestCase(TestCase):
    def setUp(self):
        call_command("seed_users", verbosity=0)
        call_command("seed_benchmark", verbosity=0)
        self.owner1 = User.objects.get(username="owner1")
        self.client = Client()

    def test_empty_state(self):
        self.client.force_login(self.owner1)
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No variance alerts")
        self.assertContains(resp, "No production runs yet")
