from datetime import date
from decimal import Decimal
from django.test import TestCase, Client
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages

from core.models import Customer, Item, Order, Invoice, Payment, StockLedger, Supplier, BOM
from core.services import (
    record_purchase,
    record_production_run,
    get_balance,
    place_order,
    invoice_outstanding,
    customer_outstanding,
)

User = get_user_model()


class AdminWebTestCase(TestCase):
    def setUp(self):
        call_command("seed_users", verbosity=0)
        call_command("seed_benchmark", verbosity=0)

        self.salesman_a = User.objects.get(username="salesman_a")
        self.salesman_b = User.objects.get(username="salesman_b")
        self.admin1 = User.objects.get(username="admin1")
        self.production1 = User.objects.get(username="production1")
        self.accountant1 = User.objects.get(username="accountant1")
        self.owner1 = User.objects.get(username="owner1")

        self.supplier = Supplier.objects.create(name="Test Supplier", gstin="33AABBCCDDEE1Z5")
        self.flour = Item.objects.get(name="Flour")
        self.oil = Item.objects.get(name="Cooking oil")
        self.spice = Item.objects.get(name="Salt and spice mix")
        self.pouch = Item.objects.get(name="200 g pouch")
        self.carton = Item.objects.get(name="Carton")
        self.murukku = Item.objects.get(name="Murukku 200 g")
        self.bom = BOM.objects.get(product=self.murukku)

        # Benchmark setup steps: buy 60 kg flour at Rs 45, run benchmark production run (188 pkts)
        record_purchase(
            self.flour,
            "60",
            "kg",
            "45",
            self.supplier,
            date(2026, 11, 24),
            invoice_no="INV-SETUP",
        )
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
        # Murukku finished goods stock is now 50 + 188 = 238
        self.assertEqual(get_balance(self.murukku), 238)

        self.retailer_a = Customer.objects.get(name="Retailer A")
        self.distributor_b = Customer.objects.get(name="Distributor B")

        self.client = Client()

    def test_admin_flow_order_to_dispatch(self):
        # salesman_a places an order for Retailer A with 150 packets
        order = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 150)])

        # admin1 opens the order page and sees the 150 and the available 238
        self.client.force_login(self.admin1)
        resp = self.client.get(f"/orders/{order.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "150")
        self.assertContains(resp, "238")
        self.assertContains(resp, "Stock OK")

        # approve through POST URL
        resp_app = self.client.post(f"/orders/{order.id}/approve/", follow=True)
        self.assertEqual(resp_app.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.APPROVED)

        # create invoice through POST URL
        resp_inv = self.client.post(f"/orders/{order.id}/create_invoice/", follow=True)
        self.assertEqual(resp_inv.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.INVOICED)
        invoice = order.invoice
        # Retailer A has state_code 33 (intra-state): taxable 750000, CGST 2.5% (18750), SGST 2.5% (18750) -> total 787500
        self.assertEqual(invoice.total_paise, 787500)

        # dispatch through POST URL
        resp_disp = self.client.post(f"/orders/{order.id}/record_dispatch/", follow=True)
        self.assertEqual(resp_disp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DISPATCHED)
        self.assertEqual(get_balance(self.murukku), 88)

    def test_payment_and_amount_validations(self):
        order = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 150)])
        self.client.force_login(self.admin1)
        self.client.post(f"/orders/{order.id}/approve/", follow=True)
        self.client.post(f"/orders/{order.id}/create_invoice/", follow=True)
        order.refresh_from_db()
        invoice = order.invoice

        # Invalid payments: amount 0, -5, 1.234
        for bad_amount in ["0", "-5", "1.234"]:
            resp = self.client.post(
                f"/invoices/{invoice.id}/record_payment/",
                {
                    "amount": bad_amount,
                    "date": "2026-11-25",
                    "mode": "BANK",
                    "reference": "TEST-FAIL",
                },
                follow=True,
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(invoice.payments.count(), 0)

        # Valid payment of Rs 4000 through the form gives outstanding 387500
        resp_pay = self.client.post(
            f"/invoices/{invoice.id}/record_payment/",
            {
                "amount": "4000",
                "date": "2026-11-25",
                "mode": "BANK",
                "reference": "REF123",
            },
            follow=True,
        )
        self.assertEqual(resp_pay.status_code, 200)
        self.assertEqual(invoice.payments.count(), 1)
        invoice.refresh_from_db()
        self.assertEqual(invoice_outstanding(invoice), 387500)
        self.assertContains(resp_pay, "3,875.00")

    def test_shortage_handling_distributor_b(self):
        # First order takes 150 packets, stock becomes 88
        order1 = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 150)])
        self.client.force_login(self.admin1)
        self.client.post(f"/orders/{order1.id}/approve/", follow=True)
        self.client.post(f"/orders/{order1.id}/create_invoice/", follow=True)
        self.client.post(f"/orders/{order1.id}/record_dispatch/", follow=True)
        self.assertEqual(get_balance(self.murukku), 88)

        # salesman_b places 120 for Distributor B
        order2 = place_order(self.distributor_b, self.salesman_b, [(self.murukku, 120)])

        # the order page says a shortage of 32 packets
        resp = self.client.get(f"/orders/{order2.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Shortage of 32 packets")

        # approve does not change the status and shows the message; no ledger row is created
        ledger_count_before = StockLedger.objects.count()
        resp_app = self.client.post(f"/orders/{order2.id}/approve/", follow=True)
        self.assertEqual(resp_app.status_code, 200)
        order2.refresh_from_db()
        self.assertEqual(order2.status, Order.Status.SUBMITTED)
        self.assertEqual(StockLedger.objects.count(), ledger_count_before)
        messages_list = [str(m) for m in get_messages(resp_app.wsgi_request)]
        self.assertTrue(any("short by 32" in m or "32" in m for m in messages_list))

    def test_role_permissions_forbidden_on_action_urls(self):
        order = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 10)])
        # Use service to approve, invoice, dispatch to get real targets
        self.client.force_login(self.admin1)
        self.client.post(f"/orders/{order.id}/approve/", follow=True)
        self.client.post(f"/orders/{order.id}/create_invoice/", follow=True)
        order.refresh_from_db()
        invoice = order.invoice

        other_users = [self.production1, self.accountant1, self.owner1, self.salesman_a]

        for user in other_users:
            self.client.force_login(user)

            # Check approve (on a new submitted order)
            sub_order = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 5)])
            resp = self.client.post(f"/orders/{sub_order.id}/approve/")
            self.assertEqual(resp.status_code, 403, f"{user.username} should get 403 on approve")
            sub_order.refresh_from_db()
            self.assertEqual(sub_order.status, Order.Status.SUBMITTED)

            # Check reject
            resp = self.client.post(f"/orders/{sub_order.id}/reject/", {"reason": "Test"})
            self.assertEqual(resp.status_code, 403, f"{user.username} should get 403 on reject")
            sub_order.refresh_from_db()
            self.assertEqual(sub_order.status, Order.Status.SUBMITTED)

            # Check create_invoice
            resp = self.client.post(f"/orders/{order.id}/create_invoice/")
            self.assertEqual(resp.status_code, 403, f"{user.username} should get 403 on create_invoice")

            # Check record_dispatch
            resp = self.client.post(f"/orders/{order.id}/record_dispatch/")
            self.assertEqual(resp.status_code, 403, f"{user.username} should get 403 on record_dispatch")

            # Check payment
            payments_count_before = invoice.payments.count()
            resp = self.client.post(
                f"/invoices/{invoice.id}/record_payment/",
                {"amount": "100", "date": "2026-11-25", "mode": "BANK"},
            )
            self.assertEqual(resp.status_code, 403, f"{user.username} should get 403 on record_payment")
            self.assertEqual(invoice.payments.count(), payments_count_before)

            # Check cancel
            resp = self.client.post(
                f"/invoices/{invoice.id}/cancel_invoice/",
                {"reason": "Test cancel"},
            )
            self.assertEqual(resp.status_code, 403, f"{user.username} should get 403 on cancel_invoice")
            invoice.refresh_from_db()
            self.assertEqual(invoice.status, Invoice.Status.ISSUED)

    def test_invoice_cancellation_with_and_without_payments(self):
        self.client.force_login(self.admin1)

        # 1. Invoice with payments: cannot be cancelled
        order1 = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 10)])
        self.client.post(f"/orders/{order1.id}/approve/", follow=True)
        self.client.post(f"/orders/{order1.id}/create_invoice/", follow=True)
        order1.refresh_from_db()
        inv1 = order1.invoice

        self.client.post(
            f"/invoices/{inv1.id}/record_payment/",
            {"amount": "100", "date": "2026-11-25", "mode": "BANK"},
            follow=True,
        )

        resp_cancel_fail = self.client.post(
            f"/invoices/{inv1.id}/cancel_invoice/",
            {"reason": "Cancel mistake"},
            follow=True,
        )
        self.assertEqual(resp_cancel_fail.status_code, 200)
        inv1.refresh_from_db()
        self.assertEqual(inv1.status, Invoice.Status.ISSUED)
        messages_list = [str(m) for m in get_messages(resp_cancel_fail.wsgi_request)]
        self.assertTrue(any("payments have already been recorded" in m for m in messages_list))

        # 2. Invoice without payments: can be cancelled
        order2 = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 10)])
        self.client.post(f"/orders/{order2.id}/approve/", follow=True)
        self.client.post(f"/orders/{order2.id}/create_invoice/", follow=True)
        order2.refresh_from_db()
        inv2 = order2.invoice

        resp_cancel_success = self.client.post(
            f"/invoices/{inv2.id}/cancel_invoice/",
            {"reason": "Order cancelled by client"},
            follow=True,
        )
        self.assertEqual(resp_cancel_success.status_code, 200)
        inv2.refresh_from_db()
        self.assertEqual(inv2.status, Invoice.Status.CANCELLED)
        self.assertContains(resp_cancel_success, "CANCELLED")

    def test_button_visibility_and_update_lines(self):
        order = place_order(self.retailer_a, self.salesman_a, [(self.murukku, 100)])
        line = order.lines.first()

        # Non-admin roles (salesman_a, production1, accountant1, owner1) do not see action buttons
        # salesman_a can view own order
        self.client.force_login(self.salesman_a)
        resp_sales = self.client.get(f"/orders/{order.id}/")
        self.assertEqual(resp_sales.status_code, 200)
        self.assertNotContains(resp_sales, "Approve")
        self.assertNotContains(resp_sales, "Reject")
        self.assertNotContains(resp_sales, "Update Lines")
        self.assertNotContains(resp_sales, "Create invoice")

        # Admin sees action buttons: Update Lines, Approve, Reject
        self.client.force_login(self.admin1)
        resp_admin = self.client.get(f"/orders/{order.id}/")
        self.assertEqual(resp_admin.status_code, 200)
        self.assertContains(resp_admin, "Update Lines")
        self.assertContains(resp_admin, "Approve")
        self.assertContains(resp_admin, "Reject")

        # Admin updates order line quantity from 100 to 120
        resp_upd = self.client.post(
            f"/orders/{order.id}/update_lines/",
            {
                f"qty_{line.id}": "120",
                f"price_{line.id}": "50.00",
            },
            follow=True,
        )
        self.assertEqual(resp_upd.status_code, 200)
        line.refresh_from_db()
        self.assertEqual(line.quantity, 120)

