from django.test import TestCase, Client
from django.core.management import call_command
from django.contrib.auth import get_user_model
from core.models import Customer, Item, ProductPrice, Order, OrderLine
from core.pdf import format_rupees

User = get_user_model()


class SalesmanWebTestCase(TestCase):
    def setUp(self):
        call_command("seed_users", verbosity=0)
        call_command("seed_benchmark", verbosity=0)

        self.salesman_a = User.objects.get(username="salesman_a")
        self.salesman_b = User.objects.get(username="salesman_b")
        self.admin1 = User.objects.get(username="admin1")
        self.production1 = User.objects.get(username="production1")
        self.accountant1 = User.objects.get(username="accountant1")
        self.owner1 = User.objects.get(username="owner1")

        self.client = Client()

    def test_anonymous_redirect(self):
        # Anonymous GET /orders/new/ redirects to /login/
        response = self.client.get("/orders/new/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/login/"))

    def test_salesman_get_order_new(self):
        # salesman_a GET /orders/new/ returns 200 and contains "Retailer A", "Distributor B" and "Murukku 200 g"
        self.client.force_login(self.salesman_a)
        response = self.client.get("/orders/new/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Retailer A")
        self.assertContains(response, "Distributor B")
        self.assertContains(response, "Murukku 200 g")

    def test_other_roles_order_new_forbidden(self):
        # admin1, production1, accountant1 and owner1 get 403 on /orders/new/
        for user in [self.admin1, self.production1, self.accountant1, self.owner1]:
            self.client.force_login(user)
            response = self.client.get("/orders/new/")
            self.assertEqual(response.status_code, 403, f"Expected 403 for {user.username}, got {response.status_code}")

    def test_salesman_post_order_success(self):
        # salesman_a POSTs 150 packets for Retailer A: an order is created for salesman_a with one line of 150
        # and an order total of 750000 paise; redirect to /orders/.
        self.client.force_login(self.salesman_a)
        retailer = Customer.objects.get(name="Retailer A")
        murukku = Item.objects.get(name="Murukku 200 g")

        post_data = {
            "customer": retailer.id,
            f"qty_{murukku.id}": "150",
        }
        response = self.client.post("/orders/new/", post_data)
        self.assertRedirects(response, "/orders/")

        order = Order.objects.filter(salesman=self.salesman_a).latest("id")
        self.assertEqual(order.customer, retailer)
        self.assertEqual(order.lines.count(), 1)
        line = order.lines.first()
        self.assertEqual(line.product, murukku)
        self.assertEqual(line.quantity, 150)
        self.assertEqual(line.unit_price_paise, 5000)
        total_paise = sum(l.quantity * l.unit_price_paise for l in order.lines.all())
        self.assertEqual(total_paise, 750000)

    def test_post_validation_errors(self):
        # POST with all quantities 0, with a negative quantity, and with no customer:
        # no Order is created and the page shows an error.
        self.client.force_login(self.salesman_a)
        retailer = Customer.objects.get(name="Retailer A")
        murukku = Item.objects.get(name="Murukku 200 g")

        initial_count = Order.objects.count()

        # All quantities 0
        resp1 = self.client.post("/orders/new/", {
            "customer": retailer.id,
            f"qty_{murukku.id}": "0",
        })
        self.assertEqual(resp1.status_code, 200)
        self.assertContains(resp1, "At least one product quantity must be greater than 0.")
        self.assertEqual(Order.objects.count(), initial_count)

        # Negative quantity
        resp2 = self.client.post("/orders/new/", {
            "customer": retailer.id,
            f"qty_{murukku.id}": "-5",
        })
        self.assertEqual(resp2.status_code, 200)
        self.assertContains(resp2, "cannot be negative")
        self.assertEqual(Order.objects.count(), initial_count)

        # No customer
        resp3 = self.client.post("/orders/new/", {
            "customer": "",
            f"qty_{murukku.id}": "10",
        })
        self.assertEqual(resp3.status_code, 200)
        self.assertContains(resp3, "Customer is required.")
        self.assertEqual(Order.objects.count(), initial_count)

    def test_order_list_isolation(self):
        # salesman_a creates an order
        self.client.force_login(self.salesman_a)
        retailer = Customer.objects.get(name="Retailer A")
        murukku = Item.objects.get(name="Murukku 200 g")

        self.client.post("/orders/new/", {
            "customer": retailer.id,
            f"qty_{murukku.id}": "50",
        })
        order = Order.objects.filter(salesman=self.salesman_a).latest("id")

        # salesman_b logs in and /orders/ does NOT show salesman_a's order
        self.client.force_login(self.salesman_b)
        resp_b = self.client.get("/orders/")
        self.assertEqual(resp_b.status_code, 200)
        self.assertNotContains(resp_b, f"#{order.id}")
        self.assertNotContains(resp_b, "Retailer A")

        # admin1 sees it
        self.client.force_login(self.admin1)
        resp_admin = self.client.get("/orders/")
        self.assertEqual(resp_admin.status_code, 200)
        self.assertContains(resp_admin, f"#{order.id}")
        self.assertContains(resp_admin, "Retailer A")
        self.assertContains(resp_admin, "salesman_a")

    def test_my_sales_view(self):
        # /my-sales/ for salesman_a after that order shows Retailer A, 1 order and Rs 7,500.00
        # salesman_b sees nothing for Retailer A; production1 gets 403.
        self.client.force_login(self.salesman_a)
        retailer = Customer.objects.get(name="Retailer A")
        murukku = Item.objects.get(name="Murukku 200 g")

        self.client.post("/orders/new/", {
            "customer": retailer.id,
            f"qty_{murukku.id}": "150",
        })

        expected_formatted = format_rupees(750000)
        self.assertEqual(expected_formatted, "7,500.00")

        resp_a = self.client.get("/my-sales/")
        self.assertEqual(resp_a.status_code, 200)
        self.assertContains(resp_a, "Retailer A")
        self.assertContains(resp_a, "1")
        self.assertContains(resp_a, f"Rs {expected_formatted}")

        # salesman_b sees nothing for Retailer A
        self.client.force_login(self.salesman_b)
        resp_b = self.client.get("/my-sales/")
        self.assertEqual(resp_b.status_code, 200)
        self.assertNotContains(resp_b, "Retailer A")

        # production1 gets 403
        self.client.force_login(self.production1)
        resp_prod = self.client.get("/my-sales/")
        self.assertEqual(resp_prod.status_code, 403)

    def test_home_page_buttons(self):
        # Home page: for a salesman show two big buttons, "New order" and "My orders", and "My sales". Keep what other roles already see.
        self.client.force_login(self.salesman_a)
        resp_salesman = self.client.get("/")
        self.assertEqual(resp_salesman.status_code, 200)
        self.assertContains(resp_salesman, "New order")
        self.assertContains(resp_salesman, "My orders")
        self.assertContains(resp_salesman, "My sales")

        self.client.force_login(self.admin1)
        resp_admin = self.client.get("/")
        self.assertEqual(resp_admin.status_code, 200)
        self.assertContains(resp_admin, "Admin Portal")
        self.assertContains(resp_admin, "Orders")
        self.assertContains(resp_admin, "Invoices")
